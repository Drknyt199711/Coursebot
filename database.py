import sqlite3

DATABASE_NAME = 'students.db'

def create_table_if_not_exists():
    """Creates the students table if it doesn't already exist."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            full_name TEXT,
            phone_number TEXT,
            course_selected TEXT,
            payment_receipt_image BLOB,
            payment_status TEXT DEFAULT 'pending',
            chat_id INTEGER,
            verification_date TEXT,
            certificate_status TEXT DEFAULT 'none',
            certificate_receipt_image BLOB,
            certificate_notified BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def add_student(user_id, full_name, phone_number, course_selected, payment_receipt_image, chat_id):
    """Adds a new student to the database or updates existing info."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO students (user_id, full_name, phone_number, course_selected, payment_receipt_image, chat_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, full_name, phone_number, course_selected, payment_receipt_image, chat_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # If the user already exists, update their pending enrollment
        cursor.execute('''
            UPDATE students
            SET full_name = ?, phone_number = ?, course_selected = ?, payment_receipt_image = ?, payment_status = ?, chat_id = ?, verification_date = NULL, certificate_status = 'none', certificate_receipt_image = NULL, certificate_notified = 0
            WHERE user_id = ?
        ''', (full_name, phone_number, course_selected, payment_receipt_image, 'pending', chat_id, user_id))
        conn.commit()
        return True
    finally:
        conn.close()

def update_payment_status(user_id, status, verification_date=None):
    """Updates a student's payment status and verification date."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    if verification_date:
        cursor.execute('''
            UPDATE students
            SET payment_status = ?, verification_date = ?
            WHERE user_id = ?
        ''', (status, verification_date, user_id))
    else:
        cursor.execute('''
            UPDATE students
            SET payment_status = ?
            WHERE user_id = ?
        ''', (status, user_id))
    conn.commit()
    conn.close()
    return True

def get_student_info(user_id):
    """Retrieves student information by user_id."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students WHERE user_id = ?', (user_id,))
    student_info = cursor.fetchone()
    conn.close()
    return student_info

def get_pending_students():
    """Retrieves all students with 'pending' payment status."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, course_selected FROM students WHERE payment_status = 'pending'")
    pending_students = cursor.fetchall()
    conn.close()
    return pending_students

def get_active_students():
    """Retrieves all students with 'verified' payment status."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, course_selected, verification_date FROM students WHERE payment_status = 'verified'")
    active_students = cursor.fetchall()
    conn.close()
    return active_students

def get_expired_students():
    """Retrieves all students with 'expired' payment status."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, course_selected, verification_date FROM students WHERE payment_status = 'expired'")
    expired_students = cursor.fetchall()
    conn.close()
    return expired_students
    
def get_verified_students_for_job():
    """Retrieves all students for the daily job to check expiry and certificate eligibility."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, chat_id, course_selected, verification_date FROM students WHERE payment_status = 'verified'")
    students = cursor.fetchall()
    conn.close()
    return students

def update_certificate_status(user_id, status):
    """Updates a student's certificate status."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE students
        SET certificate_status = ?
        WHERE user_id = ?
    ''', (status, user_id))
    conn.commit()
    conn.close()
    return True

def add_certificate_receipt(user_id, receipt_image):
    """Adds a certificate receipt to the database."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE students
        SET certificate_status = 'pending', certificate_receipt_image = ?
        WHERE user_id = ?
    ''', (receipt_image, user_id))
    conn.commit()
    conn.close()
    return True
    
def get_pending_cert_students():
    """Retrieves all students with 'pending' certificate status."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, course_selected FROM students WHERE certificate_status = 'pending'")
    pending_students = cursor.fetchall()
    conn.close()
    return pending_students

def get_certificate_receipt_image(user_id):
    """Retrieves the certificate receipt image for a user."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT certificate_receipt_image FROM students WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0]
    return None

def update_certificate_notified(user_id):
    """Sets the certificate notified flag to True."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET certificate_notified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_expired_course_students():
    """Retrieves students whose courses have expired."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, course_selected, chat_id FROM students WHERE payment_status = 'expired'")
    expired_students = cursor.fetchall()
    conn.close()
    return expired_students

def update_payment_status_to_expired(user_id):
    """Updates a student's payment status to 'expired'."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET payment_status = 'expired' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()