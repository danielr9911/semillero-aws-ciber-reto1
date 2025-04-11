import json
import boto3
import os
from datetime import datetime
from decimal import Decimal
import traceback

# Inicializar clientes de AWS
print("Inicializando clientes de AWS...")
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')

# Obtener el ARN del tema SNS desde las variables de entorno
print("Obteniendo variables de entorno...")
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE')
print(f"SNS_TOPIC_ARN: {SNS_TOPIC_ARN}")
print(f"TABLE_NAME: {TABLE_NAME}")

table = dynamodb.Table(TABLE_NAME) if TABLE_NAME else None

class DecimalEncoder(json.JSONEncoder):
    """Clase auxiliar para manejar Decimal en la serialización JSON"""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super(DecimalEncoder, self).default(o)

def check_reservation_conflicts(room_number, check_in_date, check_out_date, current_reservation_id):
    """Verifica conflictos con otras reservas"""
    print(f"\n=== VERIFICANDO CONFLICTOS ===")
    print(f"Habitación: {room_number}")
    print(f"Check-in: {check_in_date}")
    print(f"Check-out: {check_out_date}")
    print(f"ID de reserva actual: {current_reservation_id}")
    
    try:
        # Convertir fechas para comparación
        check_in = datetime.strptime(check_in_date, '%Y-%m-%d')
        check_out = datetime.strptime(check_out_date, '%Y-%m-%d')
        
        # Scan para encontrar reservas en la misma habitación
        filter_expression = "RoomNumber = :room AND ReservationID <> :rid AND #status <> :canceled"
        response = table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeValues={
                ':room': room_number,
                ':rid': current_reservation_id,
                ':canceled': 'Cancelada'
            },
            ExpressionAttributeNames={
                '#status': 'Status'
            }
        )
        
        print(f"Encontradas {len(response.get('Items', []))} reservas para verificar")
        
        # Verificar solapamientos
        conflicts = []
        for item in response.get('Items', []):
            existing_check_in = datetime.strptime(item['CheckInDate'], '%Y-%m-%d')
            existing_check_out = datetime.strptime(item['CheckOutDate'], '%Y-%m-%d')
            
            if check_in < existing_check_out and check_out > existing_check_in:
                print(f"¡CONFLICTO! con reserva {item['ReservationID']}")
                conflicts.append(item)
        
        print(f"Total de conflictos encontrados: {len(conflicts)}")
        return conflicts
    
    except Exception as e:
        print(f"ERROR en check_reservation_conflicts: {str(e)}")
        print(traceback.format_exc())
        return []

def send_conflict_notification(reservation, conflicts):
    """Envía notificación SNS sobre conflictos"""
    print(f"\n=== ENVIANDO NOTIFICACIÓN DE CONFLICTO ===")
    
    if not SNS_TOPIC_ARN:
        print("ERROR: No se ha configurado SNS_TOPIC_ARN")
        return False
    
    try:
        # Crear mensaje
        subject = f"¡ALERTA! Conflicto de reservas para la habitación {reservation['RoomNumber']}"
        
        message_body = f"""
        Se ha detectado un conflicto de reservas en el sistema del Hotel Cloud Suites.
        
        Detalles de la nueva reserva:
        - ID: {reservation['ReservationID']}
        - Habitación: {reservation['RoomNumber']}
        - Huésped: {reservation.get('GuestName', 'No disponible')}
        - Fecha de entrada: {reservation['CheckInDate']}
        - Fecha de salida: {reservation['CheckOutDate']}
        - Email: {reservation.get('ContactEmail', 'No disponible')}
        
        Esta reserva tiene conflicto con las siguientes reservas existentes:
        """
        
        for conflict in conflicts:
            message_body += f"""
        * Reserva ID: {conflict['ReservationID']}
          - Huésped: {conflict.get('GuestName', 'No disponible')}
          - Fecha de entrada: {conflict['CheckInDate']}
          - Fecha de salida: {conflict['CheckOutDate']}
          - Email: {conflict.get('ContactEmail', 'No disponible')}
        """
        
        message_body += """
        Por favor, contacte a los huéspedes para resolver este conflicto lo antes posible.
        
        Este es un mensaje automático del sistema de reservas del Hotel Cloud Suites.
        """
        
        # Enviar notificación
        print(f"Enviando a SNS Topic: {SNS_TOPIC_ARN}")
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message_body
        )
        print(f"Notificación enviada con éxito: {response['MessageId']}")
        return True
    
    except Exception as e:
        print(f"ERROR al enviar notificación SNS: {str(e)}")
        print(traceback.format_exc())
        return False

def update_reservation_status(reservation_id, new_status):
    """Actualiza el estado de una reserva en DynamoDB"""
    print(f"\n=== ACTUALIZANDO ESTADO DE RESERVA ===")
    print(f"ID: {reservation_id}, Nuevo estado: {new_status}")
    
    try:
        updated_at = datetime.now().isoformat()
        
        # IMPORTANTE: SET en mayúsculas y siempre usar #status como alias
        response = table.update_item(
            Key={'ReservationID': reservation_id},
            UpdateExpression="SET #status = :s, UpdatedAt = :u",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={
                ':s': new_status,
                ':u': updated_at
            },
            ReturnValues="UPDATED_NEW"
        )
        print(f"Actualización exitosa: {response.get('Attributes')}")
        return True
    
    except Exception as e:
        print(f"ERROR al actualizar reserva: {str(e)}")
        print(traceback.format_exc())
        return False

def process_put_item_event(event):
    """Procesa específicamente eventos PutItem"""
    print("\n=== PROCESANDO EVENTO PUT_ITEM ===")
    
    try:
        # CORRECCIÓN: Extraer el ReservationID de requestParameters.key igual que en UpdateItem
        key_data = event['detail']['requestParameters']['key']
        print(f"Datos de clave encontrados en PutItem: {json.dumps(key_data, cls=DecimalEncoder)}")
        
        # El ReservationID puede estar en diferentes formatos
        reservation_id = None
        
        # Formato 1: {'ReservationID': {'S': 'id-value'}}
        if isinstance(key_data, dict) and 'ReservationID' in key_data:
            if isinstance(key_data['ReservationID'], dict) and 'S' in key_data['ReservationID']:
                reservation_id = key_data['ReservationID']['S']
            # Formato 2: {'ReservationID': 'id-value'}
            elif isinstance(key_data['ReservationID'], str):
                reservation_id = key_data['ReservationID']
        
        print(f"ReservationID extraído del evento PutItem: {reservation_id}")
        
        if not reservation_id:
            print("ERROR: No se pudo extraer un ReservationID válido del evento PutItem")
            return None
        
        # Consultar el registro completo en DynamoDB (igual que en UpdateItem)
        print(f"Consultando DynamoDB para obtener datos completos de la reserva {reservation_id}")
        response = table.get_item(Key={'ReservationID': reservation_id})
        
        if 'Item' in response:
            print(f"Reserva completa recuperada desde DynamoDB: {json.dumps(response['Item'], cls=DecimalEncoder)}")
            return response['Item']
        else:
            print(f"ERROR: No se encontró la reserva con ID {reservation_id}")
            return None
    
    except KeyError as e:
        print(f"ERROR: No se encontró la estructura esperada en el evento PutItem")
        print(f"KeyError: {str(e)}")
        print(f"Estructura del evento: {json.dumps(event['detail'].get('requestParameters', {}), cls=DecimalEncoder)}")
        print(traceback.format_exc())
        return None
    
    except Exception as e:
        print(f"ERROR al procesar evento PutItem: {str(e)}")
        print(traceback.format_exc())
        return None

def process_update_item_event(event):
    """Procesa específicamente eventos UpdateItem"""
    print("\n=== PROCESANDO EVENTO UPDATE_ITEM ===")
    
    try:
        # En UpdateItem, necesitamos obtener el ReservationID y luego consultar el registro completo
        key_data = event['detail']['requestParameters']['key']
        print(f"Datos de clave encontrados: {json.dumps(key_data, cls=DecimalEncoder)}")
        
        # El ReservationID puede estar en diferentes formatos según cómo se hizo la llamada API
        reservation_id = None
        
        # Formato 1: {'ReservationID': {'S': 'id-value'}}
        if isinstance(key_data, dict) and 'ReservationID' in key_data:
            if isinstance(key_data['ReservationID'], dict) and 'S' in key_data['ReservationID']:
                reservation_id = key_data['ReservationID']['S']
            # Formato 2: {'ReservationID': 'id-value'}
            elif isinstance(key_data['ReservationID'], str):
                reservation_id = key_data['ReservationID']
        
        print(f"ReservationID extraído: {reservation_id}")
        
        if not reservation_id:
            print("ERROR: No se pudo extraer un ReservationID válido")
            return None
        
        # Consultar el registro completo en DynamoDB
        print(f"Consultando DynamoDB para obtener datos completos de la reserva {reservation_id}")
        response = table.get_item(Key={'ReservationID': reservation_id})
        if 'Item' in response:
            print(f"Reserva completa recuperada desde DynamoDB: {json.dumps(response['Item'], cls=DecimalEncoder)}")
            return response['Item']
        else:
            print(f"ERROR: No se encontró la reserva con ID {reservation_id}")
            return None
    
    except KeyError as e:
        print(f"ERROR: No se encontró la estructura esperada en el evento UpdateItem")
        print(f"KeyError: {str(e)}")
        print(f"Estructura del evento: {json.dumps(event['detail'].get('requestParameters', {}), cls=DecimalEncoder)}")
        print(traceback.format_exc())
        return None
    
    except Exception as e:
        print(f"ERROR al procesar evento UpdateItem: {str(e)}")
        print(traceback.format_exc())
        return None

def process_batch_write_item_event(event):
    """Procesa específicamente eventos BatchWriteItem"""
    print("\n=== PROCESANDO EVENTO BATCH_WRITE_ITEM ===")
    
    try:
        # En BatchWriteItem, las operaciones están en requestParameters.requestItems.[TableName]
        request_items = event['detail']['requestParameters']['requestItems']
        
        if TABLE_NAME not in request_items:
            print(f"ERROR: No se encontró la tabla {TABLE_NAME} en requestItems")
            return None
        
        requests = request_items[TABLE_NAME]
        print(f"Encontrados {len(requests)} requests para la tabla")
        
        if not requests or 'putRequest' not in requests[0]:
            print("ERROR: No se encontró putRequest en las solicitudes")
            return None
        
        # Extraer datos del primer elemento putRequest
        item_data = requests[0]['putRequest']['item']
        print(f"Datos del primer elemento: {json.dumps(item_data, cls=DecimalEncoder)}")
        
        # Extraer ReservationID
        if 'ReservationID' not in item_data:
            print("ERROR: No se encontró ReservationID en el item")
            return None
        
        # Puede estar en formato {'S': 'id-value'} o directo como string
        reservation_id = None
        if isinstance(item_data['ReservationID'], dict) and 'S' in item_data['ReservationID']:
            reservation_id = item_data['ReservationID']['S']
        elif isinstance(item_data['ReservationID'], str):
            reservation_id = item_data['ReservationID']
        
        print(f"ReservationID extraído: {reservation_id}")
        
        if not reservation_id:
            print("ERROR: No se pudo extraer un ReservationID válido")
            return None
        
        # Consultar la tabla para obtener el registro completo
        print(f"Consultando DynamoDB para obtener datos completos de la reserva {reservation_id}")
        response = table.get_item(Key={'ReservationID': reservation_id})
        if 'Item' in response:
            print(f"Reserva completa recuperada desde DynamoDB: {json.dumps(response['Item'], cls=DecimalEncoder)}")
            return response['Item']
        else:
            print(f"ERROR: No se encontró la reserva con ID {reservation_id}")
            return None
    
    except KeyError as e:
        print(f"ERROR: No se encontró la estructura esperada en el evento BatchWriteItem")
        print(f"KeyError: {str(e)}")
        print(f"Estructura del evento: {json.dumps(event['detail'].get('requestParameters', {}), cls=DecimalEncoder)}")
        print(traceback.format_exc())
        return None
    
    except Exception as e:
        print(f"ERROR al procesar evento BatchWriteItem: {str(e)}")
        print(traceback.format_exc())
        return None

def extract_reservation_from_cloudtrail(event):
    """Extrae datos de reserva basado en el tipo de evento"""
    print("\n=== EXTRAYENDO DATOS DE RESERVA ===")
    
    try:
        # Verificación básica de la estructura del evento
        if 'detail' not in event or 'eventName' not in event.get('detail', {}):
            print("ERROR: Evento no tiene la estructura esperada de CloudTrail")
            return None
        
        # Determinar el tipo de evento y procesarlo según corresponda
        event_name = event['detail']['eventName']
        print(f"Tipo de evento detectado: {event_name}")
        
        if event_name == 'PutItem':
            return process_put_item_event(event)
        elif event_name == 'UpdateItem':
            return process_update_item_event(event)
        elif event_name == 'BatchWriteItem':
            return process_batch_write_item_event(event)
        else:
            print(f"ADVERTENCIA: Tipo de evento no soportado: {event_name}")
            return None
    
    except Exception as e:
        print(f"ERROR general en extract_reservation_from_cloudtrail: {str(e)}")
        print(traceback.format_exc())
        return None

def lambda_handler(event, context):
    """Función principal de la Lambda"""
    print("\n========== INICIO DE EJECUCIÓN ==========")
    print(f"Evento recibido: {json.dumps(event, cls=DecimalEncoder)}")
    
    try:
        # Extraer datos de reserva según el tipo de evento
        reservation = extract_reservation_from_cloudtrail(event)
        
        # Validar que se obtuvo información válida
        if not reservation or 'ReservationID' not in reservation:
            print("ERROR: No se pudo extraer información válida de reserva")
            return {
                'statusCode': 400,
                'body': json.dumps('No se pudo procesar el evento, datos de reserva no encontrados')
            }
        
        # SOLUCIÓN LOOP INFINITO: Solo procesar reservas con estado "Pendiente"
        if reservation.get('Status') != 'Pendiente':
            print(f"⚠️ Reserva con estado '{reservation.get('Status')}'. Solo se procesan reservas pendientes.")
            return {
                'statusCode': 200,
                'body': json.dumps('Evento ignorado: No es una reserva pendiente')
            }
        
        # Verificar campos necesarios
        required_fields = ['RoomNumber', 'CheckInDate', 'CheckOutDate']
        missing_fields = [field for field in required_fields if field not in reservation]
        if missing_fields:
            print(f"ERROR: Faltan campos necesarios en la reserva: {missing_fields}")
            return {
                'statusCode': 400,
                'body': json.dumps(f'Datos de reserva incompletos. Faltan campos: {missing_fields}')
            }
        
        # Verificar si hay conflictos
        conflicts = check_reservation_conflicts(
            reservation['RoomNumber'],
            reservation['CheckInDate'],
            reservation['CheckOutDate'],
            reservation['ReservationID']
        )
        
        # Procesar según resultado de la verificación
        if conflicts:
            print(f"Se encontraron {len(conflicts)} conflictos")
            # Marcar reserva como conflictiva
            update_success = update_reservation_status(reservation['ReservationID'], 'Conflicto')
            if not update_success:
                print("ADVERTENCIA: No se pudo actualizar el estado a 'Conflicto'")
            
            # Enviar notificación
            notification_sent = send_conflict_notification(reservation, conflicts)
            if not notification_sent:
                print("ADVERTENCIA: No se pudo enviar la notificación de conflicto")
            else:
                print("Notificación de conflicto enviada correctamente")
        else:
            print("No se encontraron conflictos")
            # Si es pendiente, marcarla como confirmada
            if reservation.get('Status') == 'Pendiente':
                update_success = update_reservation_status(reservation['ReservationID'], 'Confirmada')
                if update_success:
                    print("Reserva marcada como 'Confirmada' exitosamente")
                else:
                    print("ADVERTENCIA: No se pudo actualizar el estado a 'Confirmada'")
        
        print("========== FIN DE EJECUCIÓN ==========")
        return {
            'statusCode': 200,
            'body': json.dumps('Procesamiento completado con éxito')
        }
    
    except Exception as e:
        print(f"ERROR CRÍTICO: {str(e)}")
        print(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error en el procesamiento: {str(e)}')
        }
