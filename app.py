from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
import boto3
import uuid
import os
import shutil
from datetime import datetime
from werkzeug.utils import secure_filename
import json

app = Flask(__name__)
app.secret_key = 'cloud_suites_hotel_secure_key'

# Configuración de AWS
S3_BUCKET_NAME = 'semillero-[USUARIO]-hotel-reservations' # MODIFICAR ESTE VALOR
DYNAMODB_TABLE = 'semillero-[USUARIO]-HotelReservations'
REGION_NAME = 'us-east-1'  # MODIFICAR SEGÚN LA REGIÓN UTILIZADA

# Configuración de almacenamiento local
LOCAL_STORAGE = 'local_storage'
LOCAL_DOCUMENTS_PATH = os.path.join(LOCAL_STORAGE, 'documents')

# Crear directorios si no existen
os.makedirs(LOCAL_DOCUMENTS_PATH, exist_ok=True)

# Inicializar clientes de AWS
s3 = boto3.client('s3', region_name=REGION_NAME)
dynamodb = boto3.resource('dynamodb', region_name=REGION_NAME)
table = dynamodb.Table(DYNAMODB_TABLE)

# Configuración para la carga de archivos
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB máximo

def allowed_file(filename):
    """Verifica si el archivo tiene una extensión permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    """Página principal"""
    return render_template('index.html')

@app.route('/reservations')
def list_reservations():
    """Lista todas las reservas"""
    try:
        response = table.scan()
        reservations = response.get('Items', [])
        
        # Ordenar por fecha de check-in
        reservations.sort(key=lambda x: x.get('CheckInDate', ''))
        
        return render_template('reservations.html', reservations=reservations)
    except Exception as e:
        flash(f"Error al cargar las reservas: {str(e)}", 'danger')
        return render_template('reservations.html', reservations=[])

@app.route('/reservation/new', methods=['GET', 'POST'])
def new_reservation():
    """Crea una nueva reserva"""
    if request.method == 'POST':
        # Obtener datos del formulario
        guest_name = request.form.get('guest_name')
        contact_email = request.form.get('contact_email')
        room_number = request.form.get('room_number')
        check_in_date = request.form.get('check_in_date')
        check_out_date = request.form.get('check_out_date')
        guests = request.form.get('guests')
        comments = request.form.get('comments')
        
        # Validar datos obligatorios
        if not (guest_name and contact_email and room_number and check_in_date and check_out_date):
            flash('Por favor complete todos los campos obligatorios', 'warning')
            return redirect(url_for('new_reservation'))
        
        # Generar ID único para la reserva
        reservation_id = str(uuid.uuid4())
        
        # Procesar documento de identidad si se cargó uno
        document_id = "Sin documento"
        if 'identity_document' in request.files:
            file = request.files['identity_document']
            if file and file.filename and allowed_file(file.filename):
                # Guardar localmente para visualización
                filename = secure_filename(f"{reservation_id}_{file.filename}")
                local_file_path = os.path.join(LOCAL_DOCUMENTS_PATH, filename)
                file.save(local_file_path)
                
                # Subir a S3 para respaldo
                try:
                    file.seek(0)  # Regresar al inicio del archivo
                    s3.upload_fileobj(file, S3_BUCKET_NAME, f"documents/{filename}")
                    document_id = filename
                except Exception as e:
                    flash(f"Error al subir el documento a S3: {str(e)}", 'warning')
                    # Aún así continuamos con la reserva, ya que tenemos el documento local
            elif file.filename:
                flash('Tipo de archivo no permitido. Use PDF, PNG, JPG o JPEG', 'warning')
        
        # Preparar item para DynamoDB
        reservation_item = {
            'ReservationID': reservation_id,
            'GuestName': guest_name,
            'ContactEmail': contact_email,
            'RoomNumber': room_number,
            'CheckInDate': check_in_date,
            'CheckOutDate': check_out_date,
            'Guests': guests,
            'Comments': comments if comments else "Sin comentarios",
            'DocumentID': document_id,
            'Status': 'Pendiente',
            'CreatedAt': datetime.now().isoformat(),
            'UpdatedAt': datetime.now().isoformat()
        }
        
        # Guardar en DynamoDB
        try:
            table.put_item(Item=reservation_item)
            flash('¡Reserva creada exitosamente!', 'success')
            return redirect(url_for('list_reservations'))
        except Exception as e:
            flash(f"Error al crear la reserva: {str(e)}", 'danger')
            return redirect(url_for('new_reservation'))
    
    # Si es método GET, mostrar formulario
    return render_template('new_reservation.html')

@app.route('/reservation/<reservation_id>')
def view_reservation(reservation_id):
    """Ver detalles de una reserva específica"""
    try:
        # Obtener la reserva por ID
        response = table.get_item(Key={'ReservationID': reservation_id})
        
        if 'Item' in response:
            reservation = response['Item']
            return render_template('view_reservation.html', reservation=reservation)
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
            # Obtener datos del formulario
            guest_name = request.form.get('guest_name')
            contact_email = request.form.get('contact_email')
            room_number = request.form.get('room_number')
            check_in_date = request.form.get('check_in_date')
            check_out_date = request.form.get('check_out_date')
            guests = request.form.get('guests')
            comments = request.form.get('comments')
            status = request.form.get('status', current_reservation.get('Status', 'Pendiente'))
            
            # Validar datos obligatorios
            if not (guest_name and contact_email and room_number and check_in_date and check_out_date):
                flash('Por favor complete todos los campos obligatorios', 'warning')
                return render_template('edit_reservation.html', reservation=current_reservation)
            
            # Procesar documento de identidad si se cargó uno nuevo
            document_id = current_reservation.get('DocumentID', 'Sin documento')
            if 'identity_document' in request.files:
                file = request.files['identity_document']
                if file and file.filename and allowed_file(file.filename):
                    # Guardar localmente para visualización
                    filename = secure_filename(f"{reservation_id}_{file.filename}")
                    local_file_path = os.path.join(LOCAL_DOCUMENTS_PATH, filename)
                    file.save(local_file_path)
                    
                    # Subir a S3 para respaldo
                    try:
                        file.seek(0)  # Regresar al inicio del archivo
                        s3.upload_fileobj(file, S3_BUCKET_NAME, f"documents/{filename}")
                        document_id = filename
                    except Exception as e:
                        flash(f"Error al subir el documento a S3: {str(e)}", 'warning')
                        # Aún así continuamos con la reserva, ya que tenemos el documento local
                elif file.filename:
                    flash('Tipo de archivo no permitido. Use PDF, PNG, JPG o JPEG', 'warning')
            
            # Actualizar la reserva en DynamoDB
            try:
                table.update_item(
                    Key={'ReservationID': reservation_id},
                    UpdateExpression='SET GuestName = :gn, ContactEmail = :ce, RoomNumber = :rn, ' +
                                    'CheckInDate = :ci, CheckOutDate = :co, Guests = :g, ' +
                                    'Comments = :cm, DocumentID = :di, #status = :st, UpdatedAt = :ua',
                    ExpressionAttributeNames={
                        '#status': 'Status'  # Usar alias para la palabra reservada
                    },
                    ExpressionAttributeValues={
                        ':gn': guest_name,
                        ':ce': contact_email,
                        ':rn': room_number,
                        ':ci': check_in_date,
                        ':co': check_out_date,
                        ':g': guests,
                        ':cm': comments if comments else "Sin comentarios",
                        ':di': document_id,
                        ':st': status,
                        ':ua': datetime.now().isoformat()
                    }
                )
                flash('¡Reserva actualizada exitosamente!', 'success')
                return redirect(url_for('view_reservation', reservation_id=reservation_id))
            except Exception as e:
                flash(f"Error al actualizar la reserva: {str(e)}", 'danger')
                return render_template('edit_reservation.html', reservation=current_reservation)
        
        # Si es GET, mostrar formulario con los datos actuales
        return render_template('edit_reservation.html', reservation=current_reservation)
        
    except Exception as e:
        flash(f"Error al procesar la solicitud: {str(e)}", 'danger')
        return redirect(url_for('list_reservations'))

@app.route('/reservation/delete/<reservation_id>', methods=['POST'])
def delete_reservation(reservation_id):
    """Eliminar una reserva"""
    try:
        # Eliminar la reserva de DynamoDB
        table.delete_item(Key={'ReservationID': reservation_id})
        
        flash('Reserva eliminada exitosamente', 'success')
        return redirect(url_for('list_reservations'))
    except Exception as e:
        flash(f"Error al eliminar la reserva: {str(e)}", 'danger')
        return redirect(url_for('list_reservations'))

@app.route('/rooms/availability')
def check_availability():
    """Verificar disponibilidad de habitaciones para fechas específicas"""
    check_in = request.args.get('check_in')
    check_out = request.args.get('check_out')
    
    if not check_in or not check_out:
        return jsonify({'error': 'Fechas no proporcionadas'}), 400
    
    # Consultar reservas existentes para las fechas especificadas
    try:
        # En un entorno real, esto sería una consulta más compleja a DynamoDB
        response = table.scan()
        reservations = response.get('Items', [])
        
        # Filtrar reservas que se solapan con las fechas seleccionadas
        overlapping_reservations = []
        check_in_date = datetime.strptime(check_in, '%Y-%m-%d')
        check_out_date = datetime.strptime(check_out, '%Y-%m-%d')
        
        for reservation in reservations:
            res_check_in = datetime.strptime(reservation['CheckInDate'], '%Y-%m-%d')
            res_check_out = datetime.strptime(reservation['CheckOutDate'], '%Y-%m-%d')
            
            # Verificar si hay solapamiento
            if check_in_date < res_check_out and check_out_date > res_check_in and reservation['Status'] != 'Cancelada':
                overlapping_reservations.append(reservation['RoomNumber'])
        
        # Lista de todas las habitaciones disponibles (simulación)
        all_rooms = ['101', '102', '201', '202', '301']
        available_rooms = [room for room in all_rooms if room not in overlapping_reservations]
        
        return jsonify({'available_rooms': available_rooms})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/document/<filename>')
def serve_document(filename):
    """Servir documentos locales"""
    return send_from_directory(LOCAL_DOCUMENTS_PATH, filename)

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Servir archivos estáticos"""
    return send_from_directory('static', filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
