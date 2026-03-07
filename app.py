import os
import sqlite3
import traceback
import re
import time
from urllib.parse import urlparse
from dotenv import load_dotenv
import boto3
from flask import Flask, render_template, request, redirect, url_for, session
from authlib.integrations.flask_client import OAuth

# Load environment variables from .env file
load_dotenv() 

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

def extract_email(text):
    if not text:
        return ""
    # Expected key format: RollNo_Email_FileName
    # Extract section after first "_" and stop at first "_" after "@"
    if "_" in text and "@" in text:
        _, rest = text.split("_", 1)
        at_idx = rest.find("@")
        if at_idx != -1:
            sep_idx = rest.find("_", at_idx)
            candidate = rest if sep_idx == -1 else rest[:sep_idx]
            if EMAIL_PATTERN.fullmatch(candidate):
                return candidate
    match = EMAIL_PATTERN.search(text)
    return match.group(0) if match else ""

def parse_file_key(file_name):
    if not file_name:
        return "", "", ""
    parts = file_name.split("_", 2)
    if len(parts) == 3:
        roll_no = parts[0].strip()
        email = parts[1].strip()
        raw_title = parts[2].strip()
    else:
        roll_no = ""
        email = extract_email(file_name)
        raw_title = file_name.strip()
    title = raw_title.rsplit(".", 1)[0] if "." in raw_title else raw_title
    return roll_no, email, title

def is_valid_http_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False

app = Flask(__name__)

# --- SESSION CONFIG ---
app.secret_key = os.getenv("FLASK_SECRET", "super-secret-key-123")
app.config['SESSION_COOKIE_NAME'] = 'google-login-session'
app.config['SESSION_PERMANENT'] = True

# --- TEACHER ACCESS CONTROL ---
TEACHER_ALLOWED_DOMAIN = os.getenv("TEACHER_ALLOWED_DOMAIN", "coeruniversity.ac.in").strip().lower()
TEACHER_ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.getenv("TEACHER_ALLOWED_EMAILS", "").split(",")
    if e.strip()
}
TEACHER_REQUIRED_LOCAL_FRAGMENT = os.getenv("TEACHER_REQUIRED_LOCAL_FRAGMENT", ".ca").strip().lower()

def is_teacher_allowed(user_info):
    email = (user_info or {}).get("email", "").strip().lower()
    if not email or "@" not in email:
        return False
    if email in TEACHER_ALLOWED_EMAILS:
        return True

    local_part, domain = email.split("@", 1)
    if TEACHER_ALLOWED_DOMAIN and domain != TEACHER_ALLOWED_DOMAIN:
        return False
    if TEACHER_REQUIRED_LOCAL_FRAGMENT:
        return TEACHER_REQUIRED_LOCAL_FRAGMENT in local_part
    return True

def teacher_access_denied_response(denied_email=""):
    session.clear()
    return render_template('access_denied.html', denied_email=denied_email), 403

# --- AWS S3 CONFIG ---
S3_BUCKET = os.getenv("S3_BUCKET")
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
    region_name="us-east-1"
)

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('edulink.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT NOT NULL,
            file_name TEXT NOT NULL UNIQUE,
            marks INTEGER,
            status TEXT,
            comment TEXT,
            student_name TEXT,
            roll_no TEXT,
            branch TEXT,
            project_title TEXT,
            submission_type TEXT,
            submission_url TEXT
        )
        """
    )

    # Migrate old schema (student_email as PRIMARY KEY) to id-based schema.
    cursor.execute("PRAGMA table_info(submissions)")
    cols = cursor.fetchall()
    col_names = {row[1] for row in cols}
    has_id = "id" in col_names
    old_pk_email = any(row[1] == "student_email" and row[5] == 1 for row in cols)

    if cols and (not has_id or old_pk_email):
        cursor.execute(
            """
            CREATE TABLE submissions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_email TEXT NOT NULL,
                file_name TEXT NOT NULL UNIQUE,
                marks INTEGER,
                status TEXT,
                comment TEXT,
                student_name TEXT,
                roll_no TEXT,
                branch TEXT,
                project_title TEXT,
                submission_type TEXT,
                submission_url TEXT
            )
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO submissions_new
                (student_email, file_name, marks, status, comment, student_name, roll_no, branch, project_title, submission_type, submission_url)
            SELECT
                student_email, file_name, marks, status, comment,
                COALESCE(student_name, ''),
                COALESCE(roll_no, ''),
                COALESCE(branch, ''),
                COALESCE(project_title, ''),
                'File',
                ''
            FROM submissions
            """
        )
        cursor.execute("DROP TABLE submissions")
        cursor.execute("ALTER TABLE submissions_new RENAME TO submissions")

    # Add new columns if they don't exist yet.
    cursor.execute("PRAGMA table_info(submissions)")
    columns = {row[1] for row in cursor.fetchall()}
    if "submission_type" not in columns:
        cursor.execute("ALTER TABLE submissions ADD COLUMN submission_type TEXT")
    if "submission_url" not in columns:
        cursor.execute("ALTER TABLE submissions ADD COLUMN submission_url TEXT")

    # Backfill type/url for existing rows.
    cursor.execute(
        """
        UPDATE submissions
        SET submission_type = COALESCE(submission_type, 'File'),
            submission_url = COALESCE(submission_url, '')
        """
    )

    conn.commit()
    conn.close()

init_db()

# --- GOOGLE OAUTH CONFIG ---
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
    authorize_params={'access_type': 'offline'}
)

# --- ROUTES ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login/student')
def student_login():
    session['user_type'] = 'student'
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/login/teacher')
def teacher_login():
    session['user_type'] = 'teacher'
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/callback')
def auth_callback():
    try:
        token = google.authorize_access_token()
        # Using the correct method to get user info via token
        user_info = token.get('userinfo')
        if not user_info:
            user_info = google.get('https://www.googleapis.com/oauth2/v3/userinfo').json()

        if "user_type" not in session:
            return "Login Error: user_type missing in session. Please retry login.", 400
        if not user_info or "email" not in user_info:
            return "Login Error: email not received from Google.", 400

        if session.get('user_type') == 'teacher' and not is_teacher_allowed(user_info):
            denied_email = user_info.get("email", "")
            return teacher_access_denied_response(denied_email)

        session['user'] = user_info
        if session.get('user_type') == 'teacher':
            return redirect('/tdash')
        return redirect('/sdash')
    except Exception as e:
        print("OAuth callback error:")
        traceback.print_exc()
        return f"Login Error: {str(e)}", 500

@app.route('/sdash')
def sdash():
    if 'user' not in session: return redirect('/')
    
    email = session['user']['email']
    conn = sqlite3.connect('edulink.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT marks, status, comment
        FROM submissions
        WHERE student_email=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (email,)
    )
    row = cursor.fetchone()
    conn.close()
    
    feedback = None
    if row:
        feedback = {'marks': row[0], 'status': row[1], 'comment': row[2]}
        
    return render_template('sdash.html', user=session['user'], feedback=feedback)

@app.route('/tdash')
def teacher_dashboard():
    # Only allow access if user type is teacher
    if 'user' not in session or session.get('user_type') != 'teacher':
        return redirect('/')
    if not is_teacher_allowed(session.get('user')):
        denied_email = (session.get('user') or {}).get('email', '')
        return teacher_access_denied_response(denied_email)
        
    files = []
    try:
        conn = sqlite3.connect('edulink.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, student_email, file_name, status, student_name, roll_no, branch, project_title, submission_type, submission_url
            FROM submissions
            ORDER BY id DESC
            """
        )
        rows = cursor.fetchall()
        conn.close()

        indexed = {}
        for submission_id, student_email, file_name, status, student_name, roll_no, branch, project_title, submission_type, submission_url in rows:
            parsed_roll, parsed_email, parsed_title = parse_file_key(file_name or "")
            email_for_display = student_email or parsed_email
            name_fallback = (email_for_display.split("@")[0] if email_for_display else "N/A").replace(".", " ").replace("_", " ").title()

            file_url = "#"
            if submission_type == "File" and file_name:
                try:
                    file_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': S3_BUCKET, 'Key': file_name},
                        ExpiresIn=3600
                    )
                except Exception:
                    file_url = "#"
            elif submission_type in ("GitHub", "URL") and submission_url:
                file_url = submission_url

            record = {
                'id': submission_id,
                'name': file_name or "",
                'display_name': submission_url if submission_type in ("GitHub", "URL") else (file_name or ""),
                'url': file_url,
                'email': email_for_display or "",
                'status': status or "Submitted",
                'student_name': student_name or name_fallback,
                'roll_no': roll_no or parsed_roll or "N/A",
                'branch': branch or "N/A",
                'project_title': project_title or parsed_title or "N/A",
                'submission_type': submission_type or "File",
            }
            files.append(record)
            indexed[file_name] = True

        # Also include objects present in S3 but missing in DB.
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj.get('Key', '')
                if not key or key in indexed:
                    continue
                parsed_roll, parsed_email, parsed_title = parse_file_key(key)
                file_url = "#"
                try:
                    file_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': S3_BUCKET, 'Key': key},
                        ExpiresIn=3600
                    )
                except Exception:
                    file_url = "#"
                files.append({
                    'id': None,
                    'name': key,
                    'display_name': key,
                    'url': file_url,
                    'email': parsed_email or "",
                    'status': "Uploaded",
                    'student_name': (parsed_email.split("@")[0] if parsed_email else "N/A").replace(".", " ").replace("_", " ").title(),
                    'roll_no': parsed_roll or "N/A",
                    'branch': "N/A",
                    'project_title': parsed_title or "N/A",
                    'submission_type': "File",
                })
    except Exception as e:
        print(f"S3 Error: {e}")

    return render_template('tdash.html', user=session['user'], projects=files)

@app.route('/submit_decision', methods=['POST'])
def submit_decision():
    if 'user' not in session: return redirect('/')
    if session.get('user_type') != 'teacher' or not is_teacher_allowed(session.get('user')):
        denied_email = (session.get('user') or {}).get('email', '')
        return teacher_access_denied_response(denied_email)
    
    file_name = (request.form.get('file_name') or "").strip()
    student_email = (request.form.get('student_email') or "").strip()
    marks = request.form.get('marks')
    decision = (request.form.get('decision') or "").strip()
    comment = (request.form.get('comment') or "").strip()

    normalized_email = extract_email(student_email)
    student_email = normalized_email or student_email
    if not student_email:
        student_email = extract_email(file_name)

    if not file_name or not student_email or not decision:
        return "Missing required fields.", 400

    conn = sqlite3.connect('edulink.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO submissions (student_email, file_name, marks, status, comment)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_name) DO UPDATE SET
            student_email=excluded.student_email,
            marks=excluded.marks,
            status=excluded.status,
            comment=excluded.comment
        """,
        (student_email, file_name, marks, decision, comment)
    )
    conn.commit()
    conn.close()

    return redirect('/tdash')

@app.route('/submit_project', methods=['POST'])
def submit_project():
    if 'user' not in session: return redirect('/')
    file = request.files.get('project_file')
    roll_no = (request.form.get('roll_no') or "").strip()
    student_name = (request.form.get('name') or "").strip()
    branch = (request.form.get('branch') or request.form.get('semester') or "").strip()
    project_title = (request.form.get('title') or "").strip()
    submission_type = (request.form.get('submission_type') or "File").strip()
    submission_url = (request.form.get('submission_url') or "").strip()
    
    if roll_no:
        if submission_type == "File":
            if not file or not file.filename:
                return "Please select a file for File Upload submission.", 400
            # File name format: RollNo_Email_FileName
            filename = f"{roll_no}_{session['user']['email']}_{file.filename}"
        else:
            if not submission_url or not is_valid_http_url(submission_url):
                return "Please provide a valid http/https project URL.", 400
            filename = f"LINK_{roll_no}_{session['user']['email']}_{int(time.time())}"

        try:
            if submission_type == "File":
                s3_client.upload_fileobj(file, S3_BUCKET, filename)
            conn = sqlite3.connect('edulink.db')
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO submissions
                    (student_email, file_name, marks, status, comment, student_name, roll_no, branch, project_title, submission_type, submission_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_name) DO UPDATE SET
                    student_email=excluded.student_email,
                    status=excluded.status,
                    student_name=excluded.student_name,
                    roll_no=excluded.roll_no,
                    branch=excluded.branch,
                    project_title=excluded.project_title,
                    submission_type=excluded.submission_type,
                    submission_url=excluded.submission_url
                """,
                (
                    session['user']['email'],
                    filename,
                    None,
                    "Submitted",
                    "",
                    student_name,
                    roll_no,
                    branch,
                    project_title,
                    submission_type,
                    submission_url if submission_type in ("GitHub", "URL") else "",
                )
            )
            conn.commit()
            conn.close()
            return "<h1>Project submitted!</h1><a href='/sdash'>GO BACK</a>"
        except Exception as e:
            return f"S3 Error: {str(e)}"
    return "PLEASE FILL ALL FIELDS AND SELECT A FILE!"

@app.route('/delete_project', methods=['POST'])
def delete_project():
    if 'user' not in session or session.get('user_type') != 'teacher' or not is_teacher_allowed(session.get('user')):
        denied_email = (session.get('user') or {}).get('email', '')
        return teacher_access_denied_response(denied_email)

    file_name = (request.form.get('file_name') or "").strip()
    if not file_name:
        return "Missing file name.", 400

    # Delete from S3 first (ignore if already missing).
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=file_name)
    except Exception as e:
        print(f"S3 Delete Error: {e}")

    # Delete corresponding DB row.
    conn = sqlite3.connect('edulink.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM submissions WHERE file_name=?", (file_name,))
    conn.commit()
    conn.close()

    return redirect('/tdash')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    # Localhost testing http
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(debug=True, port=5000)
