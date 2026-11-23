<?php
header('Content-Type: application/json');

$host = "sql113.infinityfree.com";
$user = "if0_43470106";
$password = "i6lnIDKogPq";
$dbname = "if0_43470106_attendance_systems";

$conn = new mysqli($host, $user, $password, $dbname);
if ($conn->connect_error) {
    echo json_encode(["status" => "error", "message" => "Database connection failed"]);
    exit();
}

$action = $_POST['action'] ?? '';

/* ------------------------------------------------------------------
   GET STUDENTS
------------------------------------------------------------------ */
if ($action == 'get_students') {
    $result = $conn->query("SELECT student_id, first_name, face_image FROM students");
    $students = [];
    while ($row = $result->fetch_assoc()) {
        $students[] = $row;
    }
    echo json_encode(["status" => "success", "students" => $students]);
}

/* ------------------------------------------------------------------
   GET ATTENDANCE (for today) — used by Python to sync state
------------------------------------------------------------------ */
elseif ($action == 'get_attendance') {
    $date = $_POST['date'] ?? date('Y-m-d');

    $stmt = $conn->prepare(
        "SELECT student_id, status, session, time_in, time_out
         FROM attendance
         WHERE DATE(time_in) = ? OR DATE(time_out) = ?"
    );
    $stmt->bind_param("ss", $date, $date);
    $stmt->execute();
    $res = $stmt->get_result();

    $attendance = [];
    while ($row = $res->fetch_assoc()) {
        $attendance[] = $row;
    }
    $stmt->close();

    echo json_encode(["status" => "success", "attendance" => $attendance]);
}

/* ------------------------------------------------------------------
   LOG ATTENDANCE  (handles TIME IN and TIME OUT)
------------------------------------------------------------------ */
elseif ($action == 'log_attendance') {
    $student_id  = $_POST['student_id'] ?? '';
    $status      = strtoupper($_POST['status'] ?? 'PRESENT');
    $session_val = $_POST['session'] ?? ($_POST['session_val'] ?? 'SESSION_1');
    $now_str     = date('Y-m-d H:i:s');

    if ($student_id === '') {
        echo json_encode(["status" => "error", "message" => "Missing student_id"]);
        $conn->close();
        exit();
    }

    // Get first_name automatically if not sent
    $sname = $_POST['sname'] ?? '';
    if (empty($sname)) {
        $stmt_name = $conn->prepare("SELECT first_name FROM students WHERE student_id = ? LIMIT 1");
        $stmt_name->bind_param("s", $student_id);
        $stmt_name->execute();
        $res_name = $stmt_name->get_result();
        if ($row_name = $res_name->fetch_assoc()) {
            $sname = $row_name['first_name'];
        }
        $stmt_name->close();
    }

    /* --------------------------------------------------------------
       TIME OUT  ->  UPDATE the matching row created at time-in
    -------------------------------------------------------------- */
    if ($status === 'TIME_OUT' || strpos($status, 'OUT') !== false) {

        // Find the latest open row for this student today
        $stmt_find = $conn->prepare(
            "SELECT id FROM attendance
             WHERE student_id = ?
               AND DATE(time_in) = CURDATE()
               AND (time_out IS NULL OR time_out = '0000-00-00 00:00:00')
             ORDER BY id DESC
             LIMIT 1"
        );
        $stmt_find->bind_param("s", $student_id);
        $stmt_find->execute();
        $res_find = $stmt_find->get_result();

        if ($row = $res_find->fetch_assoc()) {
            $att_id = $row['id'];
            $stmt_find->close();

            // UPDATE the existing row with the time_out ONLY. 
            // DO NOT change the status so it stays "PRESENT" or "LATE"
            $stmt_upd = $conn->prepare(
                "UPDATE attendance
                    SET time_out = ?
                  WHERE id = ?"
            );
            $stmt_upd->bind_param("si", $now_str, $att_id);

            if ($stmt_upd->execute()) {
                echo json_encode([
                    "status"  => "success",
                    "message" => "Time-out updated for $student_id"
                ]);
            } else {
                echo json_encode(["status" => "error", "message" => $stmt_upd->error]);
            }
            $stmt_upd->close();
        } else {
            $stmt_find->close();

            // Fallback: No open row found, insert a new record
            $stmt = $conn->prepare(
                "INSERT INTO attendance (student_id, first_name, time_in, time_out, status, session)
                 VALUES (?, ?, ?, ?, 'TIME_OUT', ?)"
            );
            $stmt->bind_param("sssss", $student_id, $sname, $now_str, $now_str, $session_val);

            if ($stmt->execute()) {
                echo json_encode([
                    "status"  => "success",
                    "message" => "Time-out (no prior time-in) logged for $student_id"
                ]);
            } else {
                echo json_encode(["status" => "error", "message" => $stmt->error]);
            }
            $stmt->close();
        }
    }

    /* --------------------------------------------------------------
       TIME IN  ->  INSERT a new row with time_out = NULL
    -------------------------------------------------------------- */
    else {
        // Prevent duplicate time-in for same student today
        $stmt_chk = $conn->prepare(
            "SELECT id FROM attendance
             WHERE student_id = ? AND DATE(time_in) = CURDATE()
             LIMIT 1"
        );
        $stmt_chk->bind_param("s", $student_id);
        $stmt_chk->execute();
        $res_chk = $stmt_chk->get_result();

        if ($res_chk->num_rows > 0) {
            $stmt_chk->close();
            echo json_encode([
                "status"  => "error",
                "message" => "Already checked in today"
            ]);
            $conn->close();
            exit();
        }
        $stmt_chk->close();

        $stmt = $conn->prepare(
            "INSERT INTO attendance (student_id, first_name, time_in, status, session)
             VALUES (?, ?, ?, ?, ?)"
        );
        $stmt->bind_param("sssss", $student_id, $sname, $now_str, $status, $session_val);

        if ($stmt->execute()) {
            echo json_encode([
                "status"  => "success",
                "message" => "Attendance logged successfully for $student_id"
            ]);
        } else {
            echo json_encode(["status" => "error", "message" => $stmt->error]);
        }
        $stmt->close();
    }
}

/* ------------------------------------------------------------------ */
else {
    echo json_encode(["status" => "error", "message" => "Invalid action"]);
}

$conn->close();
?>
