import json
import boto3
import os
from datetime import datetime
from decimal import Decimal

# Inicializar clientes de AWS
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')

# Obtener el ARN del tema SNS desde las variables de entorno
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE')
table = dynamodb.Table(TABLE_NAME)

class DecimalEncoder(json.JSONEncoder):
    """Clase auxiliar para manejar el tipo Decimal de DynamoDB en la serialización JSON"""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super(DecimalEncoder, self).default(o)

def check_reservation_conflicts(room_number, check_in_date, check_out_date, current_reservation_id):
    """
    Verifica si hay conflictos con otras reservas para la misma habitación y fechas superpuestas.
    
    Args:
        room_number (str): Número de habitación a verificar
        check_in_date (str): Fecha de entrada en formato YYYY-MM-DD
        check_out_date (str): Fecha de salida en formato YYYY-MM-DD
        current_reservation_id (str): ID de la reserva actual (para excluirla de la verificación)
        
    Returns:
        list: Lista de reservas conflictivas
    """
    # Convertir fechas de string a objetos datetime para comparación
    check_in = datetime.strptime(check_in_date, '%Y-%m-%d')
    check_out = datetime.strptime(check_out_date, '%Y-%m-%d')
    
    # Escanear la tabla para buscar todas las reservas para la habitación especificada
    response = table.scan(
        FilterExpression="RoomNumber = :room AND ReservationID <> :rid AND #status <> :canceled",
        ExpressionAttributeValues={
            ':room': room_number,
            ':rid': current_reservation_id,
            ':canceled': 'Cancelada'
        },
        ExpressionAttributeNames={
            '#status': 'Status'
        }
    )
    
    conflicts = []
    
    # Verificar cada reserva existente para detectar solapamientos de fechas
    for item in response.get('Items', []):
        existing_check_in = datetime.strptime(item['CheckInDate'], '%Y-%m-%d')
        existing_check_out = datetime.strptime(item['CheckOutDate'], '%Y-%m-%d')
        
        # Verificar si hay solapamiento de fechas
        # Hay conflicto si la nueva fecha de entrada está antes de la salida existente
        # y la nueva fecha de salida está después de la entrada existente
        if check_in < existing_check_out and check_out > existing_check_in:
            conflicts.append(item)
    
    return conflicts

def send_conflict_notification(reservation, conflicts):
    """
    Envía una notificación SNS sobre un conflicto de reservas
    
    Args:
        reservation (dict): Datos de la reserva actual
        conflicts (list): Lista de reservas conflictivas
    """
    if not SNS_TOPIC_ARN:
        print("ERROR: No se ha configurado la variable de entorno SNS_TOPIC_ARN")
        return
    
    # Crear el mensaje
    subject = f"¡ALERTA! Conflicto de reservas para la habitación {reservation['RoomNumber']}"
    
    message_body = f"""
    Se ha detectado un conflicto de reservas en el sistema del Hotel Cloud Suites.
    
    Detalles de la nueva reserva:
    - ID: {reservation['ReservationID']}
    - Habitación: {reservation['RoomNumber']}
    - Huésped: {reservation['GuestName']}
    - Fecha de entrada: {reservation['CheckInDate']}
    - Fecha de salida: {reservation['CheckOutDate']}
    - Email: {reservation['ContactEmail']}
    
    Esta reserva tiene conflicto con las siguientes reservas existentes:
    """
    
    for conflict in conflicts:
        message_body += f"""
    * Reserva ID: {conflict['ReservationID']}
      - Huésped: {conflict['GuestName']}
      - Fecha de entrada: {conflict['CheckInDate']}
      - Fecha de salida: {conflict['CheckOutDate']}
      - Email: {conflict['ContactEmail']}
    """
    
    message_body += """
    Por favor, contacte a los huéspedes para resolver este conflicto lo antes posible.
    
    Este es un mensaje automático del sistema de reservas del Hotel Cloud Suites.
    """
    
    # Enviar la notificación
    try:
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message_body
        )
        print(f"Notificación enviada: {response['MessageId']}")
    except Exception as e:
        print(f"Error al enviar la notificación: {str(e)}")

def update_reservation_status(reservation_id, new_status):
    """
    Actualiza el estado de una reserva en DynamoDB
    
    Args:
        reservation_id (str): ID de la reserva a actualizar
        new_status (str): Nuevo estado para la reserva
    """
    try:
        table.update_item(
            Key={'ReservationID': reservation_id},
            UpdateExpression="set #status = :s, UpdatedAt = :u",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={
                ':s': new_status,
                ':u': datetime.now().isoformat()
            }
        )
        print(f"Reserva {reservation_id} actualizada a estado: {new_status}")
    except Exception as e:
        print(f"Error al actualizar la reserva: {str(e)}")

def extract_reservation_from_cloudtrail(event):
    """
    Extrae los datos de reserva del evento CloudTrail via EventBridge
    
    Args:
        event: Evento de CloudTrail vía EventBridge
        
    Returns:
        dict: Datos de la reserva o None si no se puede extraer
    """
    try:
        # Acceder a los datos relevantes del evento de CloudTrail
        event_name = event['detail']['eventName']
        
        # PutItem: contiene todos los atributos del item
        if event_name == 'PutItem':
            item_data = event['detail']['requestParameters']['item']
            reservation = {}
            
            # Convertir el formato de DynamoDB a formato Python
            for key, value_dict in item_data.items():
                # Extraer el primer valor (S: String, N: Number, etc.)
                value_type = list(value_dict.keys())[0]  # 'S', 'N', etc.
                value = value_dict[value_type]
                
                # Convertir números si es necesario
                if value_type == 'N':
                    reservation[key] = Decimal(value)
                else:
                    reservation[key] = value
            
            return reservation
            
        # UpdateItem: puede contener solo algunos atributos, necesitamos obtener el item completo
        elif event_name in ['UpdateItem', 'BatchWriteItem']:
            # Obtener el ID de la reserva del evento
            if event_name == 'UpdateItem':
                key_data = event['detail']['requestParameters']['key']
                reservation_id = key_data.get('ReservationID', {}).get('S')
            else:
                # Para BatchWriteItem, extraer el primer elemento (simplificado)
                requests = event['detail']['requestParameters'].get('requestItems', {}).get(TABLE_NAME, [])
                if requests and 'putRequest' in requests[0]:
                    item_data = requests[0]['putRequest']['item']
                    reservation_id = item_data.get('ReservationID', {}).get('S')
                else:
                    return None
            
            if not reservation_id:
                return None
                
            # Consultar la base de datos para obtener el ítem completo
            response = table.get_item(Key={'ReservationID': reservation_id})
            if 'Item' in response:
                return response['Item']
                
        return None
        
    except Exception as e:
        print(f"Error al extraer datos de reserva del evento: {str(e)}")
        print(f"Evento recibido: {json.dumps(event, cls=DecimalEncoder)}")
        return None

def lambda_handler(event, context):
    """
    Función principal que se ejecuta cuando la Lambda es invocada por EventBridge
    
    Args:
        event: Evento de CloudTrail via EventBridge
        context: Contexto de la función Lambda
        
    Returns:
        dict: Resultado de la operación
    """
    print("Evento recibido:", json.dumps(event, cls=DecimalEncoder))
    
    try:
        # Extraer datos de reserva del evento
        reservation = extract_reservation_from_cloudtrail(event)
        
        if not reservation or 'ReservationID' not in reservation:
            print("No se pudo extraer información válida de reserva del evento")
            return {
                'statusCode': 400,
                'body': json.dumps('No se pudo procesar el evento, datos de reserva no encontrados')
            }
            
        print(f"Procesando reserva: {reservation['ReservationID']}")
        
        # Verificar si hay conflictos
        conflicts = check_reservation_conflicts(
            reservation['RoomNumber'],
            reservation['CheckInDate'],
            reservation['CheckOutDate'],
            reservation['ReservationID']
        )
        
        if conflicts:
            print(f"Se encontraron {len(conflicts)} conflictos")
            # Marcar la reserva como 'Conflicto' en DynamoDB
            update_reservation_status(reservation['ReservationID'], 'Conflicto')
            # Enviar notificación sobre el conflicto
            send_conflict_notification(reservation, conflicts)
        else:
            print("No se encontraron conflictos")
            # Si es una reserva pendiente, marcarla como confirmada
            if reservation.get('Status') == 'Pendiente':
                update_reservation_status(reservation['ReservationID'], 'Confirmada')
        
        return {
            'statusCode': 200,
            'body': json.dumps('Procesamiento completado con éxito')
        }
    
    except Exception as e:
        print(f"Error en el procesamiento: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error en el procesamiento: {str(e)}')
        }