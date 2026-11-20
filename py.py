import os
import time
import base64
import json
from datetime import datetime
import cv2
import face_recognition
import numpy as np
import requests

# Optional GUI editor import with safe fallback
try:
    import tkinter as tk
    from tkinter import messagebox
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

# Try importing the bypass library
try:
    from infinityfree import Bypasse
    BYPASS_AVAILABLE = True
except ImportError:
    print("[WARNING] 'infinityfree' library not installed. Falling back to standard requests (will likely fail due to firewall).")
    BYPASS_AVAILABLE = False

# --- API CONFIGURATION ---
API_URL = "https://phronesis-pre-schooler.kesug.com/api.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://phronesis-pre-schooler.kesug.com",
    "Referer": "https://phronesis-pre-schooler.kesug.com/",
}

print("[INFO] Fetching student profiles from website API...")

KNOWN_FACES_DIR = "known_faces"
os.makedirs(KNOWN_FACES_DIR, exist_ok=True)

# --- 4 SESSIONS CONFIGURATION ---
SCHEDULE_FILE = "session_schedules.json"
DEFAULT_SESSIONS = [
    {"name": "SESSION_1", "label": "Session 1", "start": "07:00:00", "end": "09:30:00", "late_after": "08:15:00"},
    {"name": "SESSION_2", "label": "Session 2", "start": "09:31:00", "end": "12:00:00", "late_after": "09:45:00"},
    {"name": "SESSION_3", "label": "Session 3", "start": "13:00:00", "end": "15:30:00", "late_after": "13:15:00"},
    {"name": "SESSION_4", "label": "Session 4", "start": "15:31:00", "end": "18:00:00", "late_after": "15:59:00"},
]

def load_session_schedules():
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) == 4:
                    return data
        except Exception as e:
            print(f"[WARN] Could not load {SCHEDULE_FILE}, using default 4 sessions: {e}")
    return [dict(s) for s in DEFAULT_SESSIONS]

def save_session_schedules(schedules):
    try:
        with open(SCHEDULE_FILE, "w") as f:
            json.dump(schedules, f, indent=2)
        print("[INFO] Saved updated 4-session schedule.")
    except Exception as e:
        print(f"[WARN] Could not save {SCHEDULE_FILE}: {e}")

session_schedules = load_session_schedules()

# Helper function to handle POST requests with or without bypass
def make_api_request(url, data):
    if BYPASS_AVAILABLE:
        return Bypasse.post(url, data=data, headers=HEADERS, timeout=15)
    else:
        return requests.post(url, data=data, headers=HEADERS, timeout=15)

# --- INITIALIZE SCANNED TRACKING & LOCAL CACHE (CHECK-IN & TIME-OUT) ---
checked_in_students = set()
timed_out_students = set()
completed_scan_keys = set()
CACHE_FILE = "scanned_cache.json"
today_date = datetime.now().strftime("%Y-%m-%d")

# Cache of student names (populated when students are fetched)
student_names = {}

# Helper function to save tracking state to local cache
def save_scanned_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "date": today_date,
                "checked_in": list(checked_in_students),
                "timed_out": list(timed_out_students),
                "completed_scan_keys": list(completed_scan_keys),
            }, f)
    except Exception as e:
        print(f"[WARN] Could not save local cache: {e}")

# Helper function to sync attendance from database and reflect deleted records
def sync_attendance_from_api(quiet=False):
    global checked_in_students, timed_out_students, completed_scan_keys, today_date
    current_day = datetime.now().strftime("%Y-%m-%d")
    if current_day != today_date:
        today_date = current_day
        checked_in_students = set()
        timed_out_students = set()
        completed_scan_keys = set()
        save_scanned_cache()

    api_attendance_synced = False
    try:
        if not quiet:
            print("[INFO] Checking database for already logged attendance today...")
        att_response = make_api_request(API_URL, {"action": "get_attendance", "date": today_date})
        att_result = att_response.json()
        if att_result.get("status") == "success":
            api_attendance_synced = True
            new_checked_in = set()
            new_timed_out = set()
            new_completed_keys = set()
            for record in att_result.get("attendance", []):
                s_id = str(record.get("student_id", "")).strip()
                status_val = str(record.get("status", "")).upper()
                sess_val = str(record.get("session", "")).upper().strip()
                if s_id:
                    if "OUT" in status_val:
                        new_timed_out.add(s_id)
                        new_checked_in.add(s_id)
                        if sess_val:
                            new_completed_keys.add(f"{today_date}:{s_id}:{sess_val}:OUT")
                    else:
                        new_checked_in.add(s_id)
                        if sess_val:
                            new_completed_keys.add(f"{today_date}:{s_id}:{sess_val}:IN")
            checked_in_students = new_checked_in
            timed_out_students = new_timed_out
            completed_scan_keys = new_completed_keys
            save_scanned_cache()
            if not quiet:
                print(f"[INFO] Synced attendance from API: {len(checked_in_students)} checked-in, {len(timed_out_students)} timed-out.")
    except Exception as e:
        if not quiet:
            print(f"[INFO] Skipped or unable to fetch existing attendance from API: {e}")

    if not api_attendance_synced and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cache_data = json.load(f)
                if cache_data.get("date") == today_date:
                    checked_in_students = set(cache_data.get("checked_in", []))
                    timed_out_students = set(cache_data.get("timed_out", []))
                    completed_scan_keys = set(cache_data.get("completed_scan_keys", []))
                    if not quiet:
                        print(f"[INFO] Loaded local cache for today: {len(checked_in_students)} checked-in, {len(timed_out_students)} timed-out.")
        except Exception as e:
            if not quiet:
                print(f"[INFO] Could not read local cache file: {e}")

# --- SYNC STUDENTS VIA API ---
synced_student_ids = set()

try:
    response = make_api_request(API_URL, {"action": "get_students"})

    print(f"[DEBUG] HTTP Status: {response.status_code}")
    print(f"[DEBUG] First 500 chars of response:\n{response.text[:500]}\n")

    try:
        result = response.json()
    except ValueError:
        result = {
            "status": "error",
            "message": "Server returned non-JSON response (InfinityFree block still active)."
        }

    if result.get("status") == "success":
        students_data = result.get("students", [])

        for s in students_data:
            s_id = str(s["student_id"])
            safe_sid = "".join(
                c if c.isalnum() or c in "-" else "" for c in s_id
            )
            if safe_sid:
                synced_student_ids.add(safe_sid)
                # Cache the name so we can send it on log_attendance
                student_names[safe_sid] = s.get("first_name", "")

            img_data = s.get("face_image")

            if not img_data:
                continue

            if isinstance(img_data, str) and img_data.startswith("data:image"):
                img_data = img_data.split(",")[1]

            raw_binary = None
            try:
                raw_binary = base64.b64decode(img_data, validate=True)
            except Exception:
                if isinstance(img_data, str):
                    possible_paths = [
                        img_data,
                        img_data.lstrip("/\\"),
                        os.path.join(os.getcwd(), img_data),
                        os.path.basename(img_data),
                        os.path.join(KNOWN_FACES_DIR, os.path.basename(img_data)),
                    ]
                    for p in possible_paths:
                        if os.path.exists(p):
                            with open(p, "rb") as f:
                                raw_binary = f.read()
                            break

            if raw_binary:
                ext = "jpg"
                if raw_binary.startswith(b"\x89PNG"):
                    ext = "png"
                elif raw_binary.startswith(b"GIF8"):
                    ext = "gif"
                elif raw_binary.startswith(b"RIFF") and raw_binary[8:12] == b"WEBP":
                    ext = "webp"

                file_path = os.path.join(KNOWN_FACES_DIR, f"{safe_sid}.{ext}")
                with open(file_path, "wb") as f:
                    f.write(raw_binary)

        # Clean up local face files for deleted students so stale profiles don't linger
        for filename in os.listdir(KNOWN_FACES_DIR):
            if filename.lower().endswith((".jpg", ".png", ".jpeg", ".webp", ".gif")):
                file_sid = os.path.splitext(filename)[0].split("_")[0]
                if file_sid not in synced_student_ids:
                    try:
                        os.remove(os.path.join(KNOWN_FACES_DIR, filename))
                        print(f"[INFO] Removed deleted student face image: {filename}")
                    except Exception as rem_err:
                        print(f"[WARN] Could not remove {filename}: {rem_err}")

        print("[INFO] Student database sync complete via API.")
    else:
        print(f"[SYNC ERROR]: {result.get('message')}")
except Exception as e:
    print(f"[SYNC CONNECTION ERROR]: {e}")

# --- LOAD KNOWN FACES INTO MEMORY ---
known_face_encodings = []
known_student_ids = []

for filename in os.listdir(KNOWN_FACES_DIR):
    if filename.lower().endswith((".jpg", ".png", ".jpeg", ".webp", ".gif")):
        student_id = os.path.splitext(filename)[0].split("_")[0]
        image_path = os.path.join(KNOWN_FACES_DIR, filename)

        try:
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            if len(encodings) > 0:
                known_face_encodings.append(encodings[0])
                known_student_ids.append(student_id)
        except Exception as e:
            print(f"[WARN] Could not load {filename}: {e}")

print(f"[INFO] Loaded {len(known_student_ids)} student face profiles.")

# Sync today's attendance from API at startup (and fall back to cache if API fails)
sync_attendance_from_api(quiet=False)

# --- 4-SESSION TIME HELPER ---
def get_current_session_info(now_dt):
    date_str = now_dt.strftime("%Y-%m-%d")
    cur_ts = now_dt.timestamp()

    def to_ts(hms_str):
        return time.mktime(
            datetime.strptime(f"{date_str} {hms_str}", "%Y-%m-%d %H:%M:%S").timetuple()
        )

    for sess in session_schedules:
        try:
            s_start = to_ts(sess["start"])
            s_end = to_ts(sess["end"])
            s_late = to_ts(sess["late_after"])
            if s_start <= cur_ts <= s_end:
                return sess["name"], cur_ts >= s_late
        except Exception:
            continue

    # If outside exact window, pick nearest/current session by time of day
    parsed_sessions = []
    for sess in session_schedules:
        try:
            parsed_sessions.append((sess["name"], to_ts(sess["start"]), to_ts(sess["late_after"])))
        except Exception:
            continue

    if parsed_sessions:
        closest = min(parsed_sessions, key=lambda item: abs(cur_ts - item[1]))
        return closest[0], cur_ts >= closest[2]

    return "SESSION_1", False

# --- EDIT 4 SESSIONS DIALOG ---
def open_edit_sessions_dialog():
    global session_schedules
    print("[INFO] Opening 4-Session Time Editor...")

    if TK_AVAILABLE:
        try:
            root = tk.Tk()
            root.title("Edit 4 Sessions Time")
            root.resizable(False, False)

            headers = ["Session", "Start (HH:MM:SS)", "End (HH:MM:SS)", "Late After (HH:MM:SS)"]
            for col_idx, h_text in enumerate(headers):
                tk.Label(root, text=h_text, font=("Arial", 10, "bold"), padx=8, pady=6).grid(row=0, column=col_idx)

            entries = []
            for row_idx, sess in enumerate(session_schedules, start=1):
                lbl = tk.Label(root, text=sess.get("label", sess["name"]), padx=8, pady=4)
                lbl.grid(row=row_idx, column=0, sticky="w")

                e_start = tk.Entry(root, width=14)
                e_start.insert(0, sess["start"])
                e_start.grid(row=row_idx, column=1, padx=4, pady=4)

                e_end = tk.Entry(root, width=14)
                e_end.insert(0, sess["end"])
                e_end.grid(row=row_idx, column=2, padx=4, pady=4)

                e_late = tk.Entry(root, width=14)
                e_late.insert(0, sess["late_after"])
                e_late.grid(row=row_idx, column=3, padx=4, pady=4)

                entries.append((sess, e_start, e_end, e_late))

            def on_save():
                updated = []
                for sess, e_start, e_end, e_late in entries:
                    st_val = e_start.get().strip()
                    en_val = e_end.get().strip()
                    lt_val = e_late.get().strip()
                    for val in (st_val, en_val, lt_val):
                        datetime.strptime(val, "%H:%M:%S")
                    new_sess = dict(sess)
                    new_sess["start"] = st_val
                    new_sess["end"] = en_val
                    new_sess["late_after"] = lt_val
                    updated.append(new_sess)

                session_schedules = updated
                save_session_schedules(session_schedules)
                messagebox.showinfo("Saved", "4-session schedule updated!")
                root.destroy()

            def on_save_safe():
                try:
                    on_save()
                except ValueError:
                    messagebox.showerror("Invalid Format", "Please use HH:MM:SS format (e.g. 08:15:00).")

            btn_frame = tk.Frame(root)
            btn_frame.grid(row=5, column=0, columnspan=4, pady=10)
            tk.Button(btn_frame, text="Save Sessions", command=on_save_safe, width=15).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Cancel", command=root.destroy, width=12).pack(side="left", padx=6)

            root.mainloop()
            return
        except Exception as gui_err:
            print(f"[WARN] Tkinter dialog unavailable ({gui_err}), falling back to console input.")

    # Console fallback
    updated = []
    for sess in session_schedules:
        print(f"\n--- {sess.get('label', sess['name'])} (Current: {sess['start']} - {sess['end']}, Late: {sess['late_after']}) ---")
        st_in = input(f"Start time HH:MM:SS [{sess['start']}]: ").strip() or sess["start"]
        en_in = input(f"End time HH:MM:SS [{sess['end']}]: ").strip() or sess["end"]
        lt_in = input(f"Late after HH:MM:SS [{sess['late_after']}]: ").strip() or sess["late_after"]
        try:
            datetime.strptime(st_in, "%H:%M:%S")
            datetime.strptime(en_in, "%H:%M:%S")
            datetime.strptime(lt_in, "%H:%M:%S")
            new_sess = dict(sess)
            new_sess["start"] = st_in
            new_sess["end"] = en_in
            new_sess["late_after"] = lt_in
            updated.append(new_sess)
        except ValueError:
            print("[WARN] Invalid time format entered; keeping previous values for this session.")
            updated.append(dict(sess))

    session_schedules = updated
    save_session_schedules(session_schedules)

# --- INTERACTIVE BUTTON STATE & MOUSE CALLBACK ---
# Modes: "AUTO" (1st scan = Time In, 2nd scan = Time Out), "IN" (force Time In), "OUT" (force Time Out)
scan_modes = ["AUTO", "IN", "OUT"]
scan_mode_index = 0
button_rects = {}
pending_edit_sessions = False
pending_sync_db = False

def on_mouse_click(event, x, y, flags, param):
    global scan_mode_index, pending_edit_sessions, pending_sync_db
    if event == cv2.EVENT_LBUTTONDOWN:
        # 1. Time In / Time Out / Auto button
        if "mode_btn" in button_rects:
            x1, y1, x2, y2 = button_rects["mode_btn"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                scan_mode_index = (scan_mode_index + 1) % len(scan_modes)
                print(f"[UI] Scan mode switched to: {scan_modes[scan_mode_index]}")
                return

        # 2. Edit 4 Sessions button
        if "edit_btn" in button_rects:
            x1, y1, x2, y2 = button_rects["edit_btn"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                pending_edit_sessions = True
                return

        # 3. Sync DB button
        if "sync_btn" in button_rects:
            x1, y1, x2, y2 = button_rects["sync_btn"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                pending_sync_db = True
                return

def draw_controls_overlay(frame, banner_text=""):
    h, w, _ = frame.shape
    current_mode = scan_modes[scan_mode_index]
    now_dt = datetime.now()
    curr_sess, _ = get_current_session_info(now_dt)

    # Top status bar
    cv2.rectangle(frame, (0, 0), (w, 36), (25, 25, 25), -1)
    cv2.putText(
        frame,
        f"Session: {curr_sess} | Time: {now_dt.strftime('%H:%M:%S')} | Mode: {current_mode}",
        (12, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )

    # Optional scan-done feedback banner below top bar
    if banner_text:
        cv2.rectangle(frame, (0, 36), (w, 68), (35, 110, 35), -1)
        cv2.putText(
            frame,
            banner_text,
            (12, 58),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            2,
        )

    # Bottom clickable buttons
    btn_y1 = max(45, h - 55)
    btn_y2 = max(85, h - 15)

    # Button 1: Time In / Time Out / Auto toggle
    m_x1, m_x2 = 15, 235
    m_color = (0, 140, 255) if current_mode == "AUTO" else ((0, 160, 0) if current_mode == "IN" else (0, 0, 200))
    cv2.rectangle(frame, (m_x1, btn_y1), (m_x2, btn_y2), m_color, -1)
    cv2.rectangle(frame, (m_x1, btn_y1), (m_x2, btn_y2), (255, 255, 255), 2)
    mode_label = "MODE: AUTO IN/OUT" if current_mode == "AUTO" else ("BUTTON: TIME IN" if current_mode == "IN" else "BUTTON: TIME OUT")
    cv2.putText(frame, mode_label, (m_x1 + 10, btn_y1 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    button_rects["mode_btn"] = (m_x1, btn_y1, m_x2, btn_y2)

    # Button 2: Edit 4 Sessions
    e_x1, e_x2 = 250, 450
    cv2.rectangle(frame, (e_x1, btn_y1), (e_x2, btn_y2), (160, 80, 20), -1)
    cv2.rectangle(frame, (e_x1, btn_y1), (e_x2, btn_y2), (255, 255, 255), 2)
    cv2.putText(frame, "EDIT 4 SESSIONS", (e_x1 + 14, btn_y1 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    button_rects["edit_btn"] = (e_x1, btn_y1, e_x2, btn_y2)

    # Button 3: Sync DB
    s_x1, s_x2 = 465, 595
    cv2.rectangle(frame, (s_x1, btn_y1), (s_x2, btn_y2), (90, 90, 90), -1)
    cv2.rectangle(frame, (s_x1, btn_y1), (s_x2, btn_y2), (255, 255, 255), 2)
    cv2.putText(frame, "SYNC DB", (s_x1 + 18, btn_y1 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    button_rects["sync_btn"] = (s_x1, btn_y1, s_x2, btn_y2)

# --- CAMERA HELPER ---
def open_camera():
    backends = []
    if hasattr(cv2, "CAP_V4L2"):
        backends.append(cv2.CAP_V4L2)
    backends.append(cv2.CAP_ANY)

    for idx in (0, 1, 2):
        for backend in backends:
            cap = cv2.VideoCapture(idx, backend) if backend != cv2.CAP_ANY else cv2.VideoCapture(idx)
            if cap.isOpened():
                # Warm-up reads in case camera needs a moment to produce frames
                for _ in range(5):
                    ok, frm = cap.read()
                    if ok and frm is not None and frm.size > 0:
                        print(f"[INFO] Camera opened on index {idx} (backend {backend}).")
                        return cap
                    time.sleep(0.1)
                cap.release()
    return None

# --- CAMERA & SCANNER LOOP ---
video_capture = open_camera()
cv2.namedWindow("Raspberry Pi Attendance Scanner", cv2.WINDOW_NORMAL)
cv2.setWindowProperty(
    "Raspberry Pi Attendance Scanner",
    cv2.WND_PROP_FULLSCREEN,
    cv2.WINDOW_FULLSCREEN,
)
cv2.setMouseCallback("Raspberry Pi Attendance Scanner", on_mouse_click)

last_scan_time = {}
COOLDOWN_SECONDS = 30
frame_count = 0
last_auto_sync_time = time.time()
AUTO_SYNC_INTERVAL_SECONDS = 30
scan_done_banner = ""
scan_done_banner_until = 0.0
consecutive_read_failures = 0
MAX_READ_FAILURES = 15

while True:
    if video_capture is None or not video_capture.isOpened():
        video_capture = open_camera()

    ret, frame = (False, None)
    if video_capture is not None:
        ret, frame = video_capture.read()

    if not ret or frame is None or frame.size == 0:
        consecutive_read_failures += 1
        print(f"[ERROR] Cannot read from camera. (Attempt {consecutive_read_failures}/{MAX_READ_FAILURES})")
        if consecutive_read_failures >= MAX_READ_FAILURES:
            if video_capture is not None:
                video_capture.release()
            video_capture = open_camera()
            consecutive_read_failures = 0
            if video_capture is None:
                print("[ERROR] Camera device unavailable. Check connection, permissions, or camera index.")
                break
        time.sleep(0.1)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
        continue

    consecutive_read_failures = 0

    # Handle button actions safely in main loop
    if pending_edit_sessions:
        pending_edit_sessions = False
        open_edit_sessions_dialog()

    if pending_sync_db:
        pending_sync_db = False
        sync_attendance_from_api(quiet=False)
        last_scan_time.clear()

    # Periodically refresh attendance state from API so deleted records take effect even without restart
    if (time.time() - last_auto_sync_time) >= AUTO_SYNC_INTERVAL_SECONDS:
        last_auto_sync_time = time.time()
        sync_attendance_from_api(quiet=True)

    frame_count += 1

    if frame_count % 2 == 0:
        small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

        face_locations = face_recognition.face_locations(rgb_small_frame)
        face_encodings = face_recognition.face_encodings(
            rgb_small_frame, face_locations
        )

        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(
                known_face_encodings, face_encoding, tolerance=0.5
            )
            face_distances = face_recognition.face_distance(
                known_face_encodings, face_encoding
            )

            if len(face_distances) > 0:
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    student_id = known_student_ids[best_match_index]
                    selected_mode = scan_modes[scan_mode_index]

                    # Determine 1 of 4 Sessions & Late status locally
                    now_dt = datetime.now()
                    today_date = now_dt.strftime("%Y-%m-%d")
                    session_val, is_late = get_current_session_info(now_dt)

                    # In AUTO mode, skip completely if the student has already checked in AND timed out today
                    if selected_mode == "AUTO" and student_id in timed_out_students:
                        continue

                    # Determine whether this scan is Time In or Time Out
                    if selected_mode == "IN":
                        do_time_in = True
                    elif selected_mode == "OUT":
                        do_time_in = False
                    else:
                        do_time_in = student_id not in checked_in_students

                    action_tag = "IN" if do_time_in else "OUT"
                    scan_key = f"{today_date}:{student_id}:{session_val}:{action_tag}"

                    # Prevent scanning over and over once this scan action is already done for this session
                    if scan_key in completed_scan_keys:
                        continue

                    current_time = time.time()

                    if student_id not in last_scan_time or (
                        current_time - last_scan_time[student_id] > COOLDOWN_SECONDS
                    ):
                        last_scan_time[student_id] = current_time

                        if do_time_in:
                            # CHECK-IN / TIME IN
                            checked_in_students.add(student_id)
                            timed_out_students.discard(student_id)
                            status = "LATE" if is_late else "PRESENT"
                            print(f"[MATCH FOUND - TIME IN ({session_val})] Student ID: {student_id} ({status}). Sending to website API...")
                        else:
                            # TIME-OUT (Leaving)
                            timed_out_students.add(student_id)
                            checked_in_students.add(student_id)
                            status = "TIME_OUT"
                            print(f"[MATCH FOUND - TIME OUT ({session_val})] Student ID: {student_id}. Sending to website API...")

                        completed_scan_keys.add(scan_key)

                        # Save updated state to local cache
                        save_scanned_cache()

                        scan_done_banner = f"SCAN DONE: Student {student_id} ({status} - {session_val})"
                        scan_done_banner_until = time.time() + 4.0

                        # Send attendance log request to API safely
                        try:
                            payload = {
                                "action": "log_attendance",
                                "student_id": student_id,
                                "status": status,
                                "session": session_val,
                                "sname": student_names.get(student_id, ""),
                            }

                            resp = make_api_request(API_URL, payload)

                            print(f"[DEBUG] Attendance HTTP: {resp.status_code}")
                            try:
                                res = resp.json()
                                print(f"[API RESPONSE]: {res.get('message')}")
                            except ValueError:
                                print(
                                    "[API ERROR]: Server returned non-JSON (Bypass failed or blocked)."
                                )
                        except Exception as api_err:
                            print(f"[API ERROR]: {api_err}")

    display_frame = frame.copy()
    active_banner = scan_done_banner if time.time() < scan_done_banner_until else ""
    draw_controls_overlay(display_frame, banner_text=active_banner)
    cv2.imshow("Raspberry Pi Attendance Scanner", display_frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    elif key == ord("e"):
        open_edit_sessions_dialog()
    elif key == ord("m"):
        scan_mode_index = (scan_mode_index + 1) % len(scan_modes)
    elif key == ord("r"):
        sync_attendance_from_api(quiet=False)
        last_scan_time.clear()

if video_capture is not None:
    video_capture.release()
cv2.destroyAllWindows()
