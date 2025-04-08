from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import boto3
import uuid
import os
from datetime import datetime
from werkzeug.utils import secure_filename
import json

app = Flask(__name__)
app.secret_key = 'cloud_suites_hotel_secure_key'

# Configuración de AWS
S3_BUCKET_NAME = 'hotel-reservations-NOMBRE-SEMILLERO' # MODIFICAR ESTE VALOR
REGION_NAME = 'us-east-1'  # MODIFICAR SEGÚN LA REGIÓN UTILIZADA

# Inicializar clientes de AWS
s3 = boto3.client('s3', region_name=REGION_NAME)
dynamodb = boto3.resource('dynamodb', region_name=REGION_NAME)
table = dynamodb.Table('HotelReservations')

# Configuración para la carga de archivos
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB max

def allowed_file(filename):
    """Verifica si la extensión del archivo es válida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    """Página principal del sistema"""
    return render_template('index.html')

@app.route('/reservations')
def list_reservations():
    """Lista todas las reservas existentes"""
    try:
        # Escanear la tabla para obtener todas las reservas
        response = table.scan()
        reservations = response.get('Items', [])
        
        # Ordenar por fecha de check-in
        reservations.sort(key=lambda x: x.get('CheckInDate', ''))
        
        return render_template('reservations.html', 
                                  reservations=reservations, 
                                  S3_BUCKET_NAME=S3_BUCKET_NAME,
                                  STATIC_PATH="statics/rooms/")
    except Exception as e:
        flash(f"Error al obtener las reservas: {str(e)}", 'danger')
        return render_template('reservations.html', 
                                  reservations=[], 
                                  S3_BUCKET_NAME=S3_BUCKET_NAME,
                                  STATIC_PATH="statics/rooms/"))

@app.route('/reservation/new', methods=['GET', 'POST'])
def new_reservation():
    """Crear una nueva reserva"""
    if request.method == 'POST':
        try:
            # Recoger datos del formulario
            guest_name = request.form.get('guest_name')
            room_number = request.form.get('room_number')
            check_in_date = request.form.get('check_in_date')
            check_out_date = request.form.get('check_out_date')
            contact_email = request.form.get('contact_email')
            
            # Validación básica
            if not all([guest_name, room_number, check_in_date, check_out_date, contact_email]):
                flash('Todos los campos son obligatorios', 'danger')
                return redirect(url_for('new_reservation'))
                
            # Validar fechas
            try:
                check_in = datetime.strptime(check_in_date, '%Y-%m-%d')
                check_out = datetime.strptime(check_out_date, '%Y-%m-%d')
                if check_in >= check_out:
                    flash('La fecha de salida debe ser posterior a la fecha de entrada', 'danger')
                    return redirect(url_for('new_reservation'))
            except ValueError:
                flash('Formato de fecha inválido', 'danger')
                return redirect(url_for('new_reservation'))
            
            # Generar ID único para la reserva
            reservation_id = str(uuid.uuid4())
            document_id = None
            
            # Gestionar carga de documento de identidad
            if 'identity_document' in request.files:
                file = request.files['identity_document']
                if file and file.filename and allowed_file(file.filename):
                    # Asegurar nombre de archivo y determinar ruta
                    filename = secure_filename(file.filename)
                    file_extension = filename.rsplit('.', 1)[1].lower()
                    document_id = f"{reservation_id}.{file_extension}"
                    s3_path = f"documents/{document_id}"
                    
                    # Subir archivo a S3
                    s3.upload_fileobj(
                        file,
                        S3_BUCKET_NAME,
                        s3_path,
                        ExtraArgs={'ContentType': f'application/{file_extension}'}
                    )
            
            # Crear registro en DynamoDB
            reservation_item = {
                'ReservationID': reservation_id,
                'RoomNumber': room_number,
                'GuestName': guest_name,
                'CheckInDate': check_in_date,
                'CheckOutDate': check_out_date,
                'ContactEmail': contact_email,
                'Status': 'Pendiente',
                'CreatedAt': datetime.now().isoformat(),
                'DocumentID': document_id if document_id else 'Sin documento'
            }
            
            table.put_item(Item=reservation_item)
            
            flash('Reserva creada exitosamente. En breve recibirá un correo de confirmación.', 'success')
            return redirect(url_for('list_reservations'))
            
        except Exception as e:
            flash(f"Error al crear la reserva: {str(e)}", 'danger')
            return redirect(url_for('new_reservation'))
    
    # Si es GET, mostrar el formulario
    return render_template('new_reservation.html')

@app.route('/reservation/<reservation_id>')
def view_reservation(reservation_id):
    """Ver detalles de una reserva específica"""
    try:
        # Obtener la reserva por ID
        response = table.get_item(Key={'ReservationID': reservation_id})
        
        if 'Item' in response:
            reservation = response['Item']
            return render_template('view_reservation.html', 
                                  reservation=reservation, 
                                  S3_BUCKET_NAME=S3_BUCKET_NAME,
                                  STATIC_PATH="statics/rooms/")
        else:
            flash('Reserva no encontrada', 'warning')
            return redirect(url_for('list_reservations'))
            
    except Exception as e:
        flash(f"Error al obtener la reserva: {str(e)}", 'danger')
        return redirect(url_for('list_reservations'))

@app.route('/reservation/edit/<reservation_id>', methods=['GET', 'POST'])
def edit_reservation(reservation_id):
    """Editar una reserva existente"""
    try:
        # Obtener la reserva actual
        response = table.get_item(Key={'ReservationID': reservation_id})
        
        if 'Item' not in response:
            flash('Reserva no encontrada', 'warning')
            return redirect(url_for('list_reservations'))
            
        current_reservation = response['Item']
        
        if request.method == 'POST':
            # Recoger datos del formulario
            guest_name = request.form.get('guest_name')
            room_number = request.form.get('room_number')
            check_in_date = request.form.get('check_in_date')
            check_out_date = request.form.get('check_out_date')
            contact_email = request.form.get('contact_email')
            
            # Validación básica
            if not all([guest_name, room_number, check_in_date, check_out_date, contact_email]):
                flash('Todos los campos son obligatorios', 'danger')
                return render_template('edit_reservation.html', 
                                       reservation=current_reservation, 
                                       S3_BUCKET_NAME=S3_BUCKET_NAME,
                                       STATIC_PATH="statics/rooms/")
                
            # Validar fechas
            try:
                check_in = datetime.strptime(check_in_date, '%Y-%m-%d')
                check_out = datetime.strptime(check_out_date, '%Y-%m-%d')
                if check_in >= check_out:
                    flash('La fecha de salida debe ser posterior a la fecha de entrada', 'danger')
                    return render_template('edit_reservation.html', 
                                            reservation=current_reservation,
                                            S3_BUCKET_NAME=S3_BUCKET_NAME,
                                            STATIC_PATH="statics/rooms/")
            except ValueError:
                flash('Formato de fecha inválido', 'danger')
                return render_template('edit_reservation.html', 
                                        reservation=current_reservation
                                        S3_BUCKET_NAME=S3_BUCKET_NAME,
                                        STATIC_PATH="statics/rooms/")
            
            document_id = current_reservation.get('DocumentID', 'Sin documento')
            
            # Gestionar carga de nuevo documento de identidad
            if 'identity_document' in request.files:
                file = request.files['identity_document']
                if file and file.filename and allowed_file(file.filename):
                    # Asegurar nombre de archivo y determinar ruta
                    filename = secure_filename(file.filename)
                    file_extension = filename.rsplit('.', 1)[1].lower()
                    document_id = f"{reservation_id}.{file_extension}"
                    s3_path = f"documents/{document_id}"
                    
                    # Subir archivo a S3
                    s3.upload_fileobj(
                        file,
                        S3_BUCKET_NAME,
                        s3_path,
                        ExtraArgs={'ContentType': f'application/{file_extension}'}
                    )
            
            # Actualizar registro en DynamoDB
            try:
                response = table.update_item(
                    Key={'ReservationID': reservation_id},
                    UpdateExpression="set RoomNumber=:r, GuestName=:g, CheckInDate=:ci, CheckOutDate=:co, ContactEmail=:e, DocumentID=:d, #status=:s, UpdatedAt=:u",
                    ExpressionAttributeNames={'#status': 'Status'},
                    ExpressionAttributeValues={
                        ':r': room_number,
                        ':g': guest_name,
                        ':ci': check_in_date,
                        ':co': check_out_date,
                        ':e': contact_email,
                        ':d': document_id,
                        ':s': 'Pendiente',  # Resetear el estado para que sea validado nuevamente
                        ':u': datetime.now().isoformat()
                    },
                    ReturnValues="UPDATED_NEW"
                )
                
                flash('Reserva actualizada exitosamente', 'success')
                return redirect(url_for('view_reservation', reservation_id=reservation_id))
                
            except Exception as e:
                flash(f"Error al actualizar la reserva: {str(e)}", 'danger')
                return render_template('edit_reservation.html', 
                                        reservation=current_reservation, 
                                        S3_BUCKET_NAME=S3_BUCKET_NAME,
                                        STATIC_PATH="statics/rooms/")
        
        # Si es GET, mostrar el formulario con los datos actuales
        return render_template('edit_reservation.html', 
                                reservation=current_reservation,
                                S3_BUCKET_NAME=S3_BUCKET_NAME,
                                STATIC_PATH="statics/rooms/")
        
    except Exception as e:
        flash(f"Error al obtener la reserva: {str(e)}", 'danger')
        return redirect(url_for('list_reservations'))

@app.route('/reservation/delete/<reservation_id>', methods=['POST'])
def delete_reservation(reservation_id):
    """Eliminar una reserva"""
    try:
        # Obtener información de la reserva para poder eliminar documentos relacionados
        response = table.get_item(Key={'ReservationID': reservation_id})
        
        if 'Item' in response and response['Item'].get('DocumentID') != 'Sin documento':
            # Eliminar el documento de S3 si existe
            document_id = response['Item']['DocumentID']
            s3_path = f"documents/{document_id}"
            try:
                s3.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_path)
            except Exception as e:
                print(f"Error al eliminar documento de S3: {str(e)}")
        
        # Eliminar la reserva de DynamoDB
        table.delete_item(Key={'ReservationID': reservation_id})
        
        flash('Reserva eliminada exitosamente', 'success')
        return redirect(url_for('list_reservations'))
        
    except Exception as e:
        flash(f"Error al eliminar la reserva: {str(e)}", 'danger')
        return redirect(url_for('list_reservations'))

@app.route('/reservation/cancel/<reservation_id>', methods=['POST'])
def cancel_reservation(reservation_id):
    """Cancelar una reserva (cambiar estado)"""
    try:
        # Actualizar el estado a "Cancelada"
        response = table.update_item(
            Key={'ReservationID': reservation_id},
            UpdateExpression="set #status=:s, UpdatedAt=:u",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={
                ':s': 'Cancelada',
                ':u': datetime.now().isoformat()
            },
            ReturnValues="UPDATED_NEW"
        )
        
        flash('Reserva cancelada exitosamente', 'success')
        return redirect(url_for('view_reservation', reservation_id=reservation_id))
        
    except Exception as e:
        flash(f"Error al cancelar la reserva: {str(e)}", 'danger')
        return redirect(url_for('view_reservation', reservation_id=reservation_id))

@app.route('/rooms/availability', methods=['GET'])
def check_availability():
    """API para verificar disponibilidad de habitaciones"""
    try:
        check_in = request.args.get('check_in')
        check_out = request.args.get('check_out')
        
        if not check_in or not check_out:
            return jsonify({'error': 'Se requieren fechas de entrada y salida'}), 400
            
        # Convertir fechas para comparación
        try:
            check_in_date = datetime.strptime(check_in, '%Y-%m-%d')
            check_out_date = datetime.strptime(check_out, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Formato de fecha inválido. Use YYYY-MM-DD'}), 400
            
        # Obtener todas las reservas activas
        response = table.scan(
            FilterExpression="#status <> :canceled",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={':canceled': 'Cancelada'}
        )
        
        # Habitaciones disponibles (ejemplo: del 101 al 120)
        all_rooms = [str(i) for i in range(101, 121)]
        booked_rooms = set()
        
        # Encontrar habitaciones ocupadas
        for item in response.get('Items', []):
            room = item['RoomNumber']
            room_check_in = datetime.strptime(item['CheckInDate'], '%Y-%m-%d')
            room_check_out = datetime.strptime(item['CheckOutDate'], '%Y-%m-%d')
            
            # Verificar si hay solapamiento de fechas
            if check_in_date < room_check_out and check_out_date > room_check_in:
                booked_rooms.add(room)
                
        # Calcular habitaciones disponibles
        available_rooms = [room for room in all_rooms if room not in booked_rooms]
        
        return jsonify({
            'check_in': check_in,
            'check_out': check_out,
            'available_rooms': available_rooms,
            'booked_rooms': list(booked_rooms)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def page_not_found(e):
    """Manejador para errores 404"""
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Manejador para errores 500"""
    return render_template('500.html'), 500

if __name__ == '__main__':
    # Crear carpeta temporal para archivos subidos si no existe
    os.makedirs('tmp', exist_ok=True)
    # Ejecutar la aplicación en modo debug, accesible desde cualquier dirección IP en el puerto 5000
    app.run(host='0.0.0.0', port=5000, debug=True)