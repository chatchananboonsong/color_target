import time
import csv
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import numpy as np
from robomaster import robot, blaster, led

# ป้องกันปัญหาแสดงผลภาษาไทยผิดพลาดบน Windows Terminal
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ==============================================================================
# RoboMaster Color & Shape Target System (GUI Target Sequencing & Real Water Fire)
# ระบบเลือกลำดับเป้าหมายสีและรูปทรงผ่านหน้าต่าง GUI และยิงเฉพาะกระสุนเจลจริง (WATER_FIRE)
# รองรับการตรวจจับเป้าหมายชนิดเดียวกันซ้ำกัน (เช่น แดง กลม 2 อัน) และแยกเป็นลำดับ 1, 2, 3, 4 จากซ้ายไปขวา
# ==============================================================================

# --------------------------------------------------
# คลาสสำหรับคำนวณ PID Controller
# --------------------------------------------------
class PIDController:
    def __init__(self, kp, ki, kd, limits=(-100, 100)):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.min_limit, self.max_limit = limits
        self.last_error = 0.0
        self.integral = 0.0
        self.last_time = time.time()

    def compute(self, error):
        now = time.time()
        dt = now - self.last_time
        if dt <= 0:
            dt = 0.01

        p_term = self.kp * error
        self.integral += error * dt
        self.integral = max(-50.0, min(50.0, self.integral))
        i_term = self.ki * self.integral
        derivative = (error - self.last_error) / dt
        d_term = self.kd * derivative

        output = p_term + i_term + d_term
        output = max(self.min_limit, min(self.max_limit, output))

        self.last_error = error
        self.last_time = now
        return output

    def reset(self):
        self.last_error = 0.0
        self.integral = 0.0
        self.last_time = time.time()


# --------------------------------------------------
# พารามิเตอร์ PID และการควบคุม
# --------------------------------------------------
pid_yaw = PIDController(kp=110.0, ki=0.01, kd=5.0, limits=(-150, 150))
pid_pitch = PIDController(kp=90.0, ki=0.0, kd=5.0, limits=(-100, 100))

# --------------------------------------------------
# การตั้งค่าการยิง: ยิงเฉพาะกระสุนเจลจริง (WATER_FIRE) เท่านั้น
# --------------------------------------------------
ENABLE_FIRE = True                 # True = เปิดระบบยิงจริง, False = เล็งอย่างเดียวเพื่อความปลอดภัย
FIRE_TYPE = blaster.WATER_FIRE     # ยิงเฉพาะกระสุนเจลจริง (WATER_FIRE) เท่านั้น
AUTO_FIRE_ENABLED = True           # True = ยิงอัตโนมัติเมื่อล็อกเป้านิ่งตามเวลาที่กำหนด

LOCK_TOLERANCE_X = 0.001           # ความแม่นยำแนวนอน (2.0% จากกึ่งกลางจอ)
LOCK_TOLERANCE_Y = 0.001           # ความแม่นยำแนวตั้ง (2.0% จากกึ่งกลางจอ)
LOCK_HOLD_TIME = 0.35              # หน่วงเวลานิ่งก่อนยิง (วินาที)
AUTO_FIRE_COOLDOWN = 1.2           # พักหลังยิงกระสุนจริง ก่อนเริ่มเล็งเป้าถัดไป (วินาที)
MIN_TARGET_AREA = 700              # กรอง noise ขนาดต่ำสุด (พิกเซล)

CONFIG_FILE = "hsv_config.json"

# ลำดับเป้าหมายที่เลือกไว้จาก GUI เช่น [("Red", "Circle", 1), ("Red", "Circle", 2)]
ordered_sequence = []

# โหมดการทำงาน: "LEARNING" (สแกน/พรีวิวลำดับ) -> "TRACKING" (เล็งและยิงกระสุนจริงตามลำดับ)
app_mode = "LEARNING"
learned_sequence = []
current_step = 0

is_shooting = False
is_firing_now = 0
last_active_label = "None"
lock_start_time = None

# บันทึกข้อมูล Time Response (CSV)
data_log = []
start_record_time = 0.0
current_pitch_angle = 0.0
current_yaw_angle = 0.0

# นิยามสีและรูปทรงทั้งหมด
AVAILABLE_COLORS = [
    ("Red", "สีแดง", "#FF4444", (0, 0, 255)),
    ("Green", "สีเขียว", "#2ECC71", (0, 255, 0)),
    ("Blue", "สีน้ำเงิน", "#3498DB", (255, 130, 0)),
    ("Yellow", "สีเหลือง", "#F1C40F", (0, 220, 255))
]

AVAILABLE_SHAPES = [
    ("Circle", "ทรงกลม", "🔘"),
    ("Square", "สี่เหลี่ยมจัตุรัส", "⬛"),
    ("Rect_H", "ผืนผ้านอน", "▰"),
    ("Rect_V", "ผืนผ้าตั้ง", "▮")
]

SHAPE_TH_MAP = {
    "Circle": "ทรงกลม",
    "Square": "สี่เหลี่ยมจัตุรัส",
    "Rect_H": "ผืนผ้านอน",
    "Rect_V": "ผืนผ้าตั้ง",
    "Rectangle": "สี่เหลี่ยม"
}


def load_hsv_configs():
    """โหลดค่า Lower และ Upper จาก hsv_config.json"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw_cfg = json.load(f)
            configs = {}
            for color_name, data in raw_cfg.items():
                ranges = []
                for r in data.get("ranges", []):
                    ranges.append((
                        np.array(r["lower"], dtype=np.uint8),
                        np.array(r["upper"], dtype=np.uint8)
                    ))
                configs[color_name] = {
                    "ranges": ranges,
                    "draw_color": tuple(data.get("draw_color", [0, 255, 0])),
                    "led_rgb": tuple(data.get("led_rgb", [255, 255, 255])),
                    "name_th": data.get("name_th", color_name)
                }
            print(f">> [CONFIG] โหลดค่า Lower/Upper HSV จาก '{CONFIG_FILE}' สำเร็จ")
            return configs
        except Exception as e:
            print(f"[!] ไม่สามารถอ่าน {CONFIG_FILE}: {e}")

    # Fallback เริ่มต้น
    return {
        "Red": {"ranges": [(np.array([0, 100, 70]), np.array([10, 255, 255])), (np.array([165, 100, 70]), np.array([180, 255, 255]))], "draw_color": (0, 0, 255), "led_rgb": (255, 0, 0), "name_th": "แดง"},
        "Green": {"ranges": [(np.array([35, 80, 70]), np.array([85, 255, 255]))], "draw_color": (0, 255, 0), "led_rgb": (0, 255, 0), "name_th": "เขียว"},
        "Blue": {"ranges": [(np.array([95, 100, 70]), np.array([130, 255, 255]))], "draw_color": (255, 130, 0), "led_rgb": (0, 100, 255), "name_th": "น้ำเงิน"},
        "Yellow": {"ranges": [(np.array([20, 100, 100]), np.array([35, 255, 255]))], "draw_color": (0, 220, 255), "led_rgb": (255, 255, 0), "name_th": "เหลือง"}
    }


COLOR_CONFIGS = load_hsv_configs()


def on_gimbal_angle(angle_info):
    """Callback บันทึกมุม Pitch, Yaw จาก Gimbal"""
    global current_pitch_angle, current_yaw_angle, data_log, start_record_time
    pitch_angle, yaw_angle, pitch_ground, yaw_ground = angle_info
    current_pitch_angle = pitch_angle
    current_yaw_angle = yaw_angle

    if start_record_time > 0 and app_mode == "TRACKING":
        elapsed = time.time() - start_record_time
        data_log.append([
            round(elapsed, 4),
            round(pitch_angle, 2),
            round(yaw_angle, 2),
            last_active_label,
            is_firing_now
        ])


def classify_shape(cnt):
    """
    ตรวจสอบรูปทรง:
    - Circle: ทรงกลม
    - Rect_H: ผืนผ้านอน (bw > bh)
    - Rect_V: ผืนผ้าตั้ง (bh > bw)
    - Square: สี่เหลี่ยมจัตุรัส
    """
    area = cv2.contourArea(cnt)
    if area < MIN_TARGET_AREA:
        return None, None

    perimeter = cv2.arcLength(cnt, True)
    if perimeter == 0:
        return None, None

    circularity = (4.0 * np.pi * area) / (perimeter * perimeter)
    _, radius = cv2.minEnclosingCircle(cnt)
    circle_area = np.pi * (radius ** 2)
    circle_ratio = area / circle_area if circle_area > 0 else 0

    approx = cv2.approxPolyDP(cnt, 0.038 * perimeter, True)
    bx, by, bw, bh = cv2.boundingRect(cnt)
    bbox_area = bw * bh
    extent = area / bbox_area if bbox_area > 0 else 0

    is_4_corners = (len(approx) == 4) and cv2.isContourConvex(approx)

    if circularity >= 0.70 and circle_ratio >= 0.68:
        return "Circle", approx

    if (is_4_corners and extent >= 0.65) or (extent >= 0.76 and len(approx) in (4, 5)):
        aspect_ratio = float(bw) / float(bh)
        if aspect_ratio >= 1.18:
            return "Rect_H", approx
        elif aspect_ratio <= 0.85:
            return "Rect_V", approx
        else:
            return "Square", approx

    return None, None


def detect_all_targets(img):
    """
    ค้นหาและแยกแยะเป้าหมายทั้งหมดในเฟรมภาพ เรียงจากซ้ายไปขวา
    หากพบเป้าหมายชนิดเดียวกัน (สี + รูปทรงเดียวกัน) จะนับและจำลำดับเป็น 1, 2, 3, 4 จากซ้ายไปขวา
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, w, _ = img.shape
    all_targets = []

    for color_name, color_cfg in COLOR_CONFIGS.items():
        combined_mask = None
        for lower, upper in color_cfg["ranges"]:
            mask_part = cv2.inRange(hsv, lower, upper)
            if combined_mask is None:
                combined_mask = mask_part
            else:
                combined_mask = cv2.bitwise_or(combined_mask, mask_part)

        kernel = np.ones((5, 5), np.uint8)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_DILATE, kernel)

        contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        for cnt in contours:
            shape_type, approx = classify_shape(cnt)
            if shape_type is None:
                continue

            M = cv2.moments(cnt)
            if M["m00"] == 0:
                continue

            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            bx, by, bw, bh = cv2.boundingRect(cnt)

            all_targets.append({
                "color": color_name,
                "shape": shape_type,
                "label": f"{color_name} {shape_type}",
                "name_th": f"{SHAPE_TH_MAP.get(shape_type, shape_type)}{color_cfg['name_th']}",
                "center": (cx, cy),
                "norm_x": cx / w,
                "norm_y": cy / h,
                "bbox": (bx, by, bw, bh),
                "contour": cnt,
                "approx": approx,
                "draw_color": color_cfg["draw_color"],
                "led_rgb": color_cfg["led_rgb"],
                "area": cv2.contourArea(cnt)
            })

    # เรียงลำดับเป้าหมายทั้งหมดจากซ้ายไปขวาตามพิกัด X
    all_targets.sort(key=lambda t: t["center"][0])

    # กำหนด sub_index ลำดับ 1, 2, 3, 4 ให้กับเป้าหมายที่เป็นชนิดเดียวกันจากซ้ายไปขวา
    type_counts = {}
    for t in all_targets:
        key = (t["color"], t["shape"])
        type_counts[key] = type_counts.get(key, 0) + 1
        t["sub_index"] = type_counts[key]
        t["label"] = f"{t['color']} {t['shape']} #{t['sub_index']}"
        t["name_th"] = f"{SHAPE_TH_MAP.get(t['shape'], t['shape'])}{COLOR_CONFIGS[t['color']]['name_th']} #{t['sub_index']}"

    return all_targets


def fire_water_bullet(ep_blaster, ep_led):
    """ยิงกระสุนเจลจริง 1 นัด (WATER_FIRE) เท่านั้น พร้อมแสดงไฟสถานะ"""
    global is_firing_now
    if not ENABLE_FIRE:
        print("\n>> [FIRE OFF] ปิดระบบยิง (อยู่ในโหมด Aim Only เพื่อความปลอดภัย)")
        return

    is_firing_now = 1
    print("\n💥 >> [WATER FIRE!] สั่งยิงกระสุนเจลจริง (WATER_FIRE) 1 นัด! << 💥")

    # ไฟเตือนสีแดงขณะยิงกระสุนจริง
    ep_led.set_led(comp=led.COMP_TOP_ALL, r=255, g=0, b=0, effect=led.EFFECT_ON)
    ep_blaster.set_led(brightness=255, effect=blaster.LED_ON)

    # ยิงกระสุนเจลจริงเท่านั้น
    try:
        ep_blaster.fire(fire_type=blaster.WATER_FIRE, times=1)
    except Exception as e:
        print(f"[!] ยิงกระสุนเจลล้มเหลว: {e}")

    time.sleep(0.35)

    ep_blaster.set_led(brightness=0, effect=blaster.LED_OFF)
    ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)

    is_firing_now = 0
    pid_yaw.reset()
    pid_pitch.reset()


def shoot_and_advance_step(ep_blaster, ep_led, auto=True):
    """ยิงกระสุนเจลจริงแล้วสลับไปเป้าหมายถัดไปในลำดับที่เลือกไว้"""
    global current_step, is_shooting

    is_shooting = True
    fire_water_bullet(ep_blaster, ep_led)

    if len(learned_sequence) > 0:
        prev_idx = current_step
        current_step = (current_step + 1) % len(learned_sequence)
        mode_tag = "AUTO" if auto else "MANUAL"
        next_tgt = learned_sequence[current_step]
        next_sub = next_tgt.get('sub_index', 1)
        print(f">> [ADVANCE-{mode_tag}] ยิงเป้า #{prev_idx+1} สำเร็จ -> สลับไปเป้า #{current_step+1}/{len(learned_sequence)}: [{next_tgt['color']} {next_tgt['shape']} #{next_sub}] (อันที่ {next_sub} จากซ้าย)")

    time.sleep(0.2)
    is_shooting = False


def build_sequence_from_pairs(pairs):
    """
    แปลงรายการ (color, shape, [sub_index]) ให้เป็นโครงสร้างข้อมูลลำดับเป้าหมาย
    คำนวณและจำลำดับ 1, 2, 3, 4 สำหรับเป้าหมายที่ซ้ำกันจากซ้ายไปขวา
    """
    seq = []
    type_counts = {}
    for item in pairs:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        c_name, s_name = item[0], item[1]

        # หากมี sub_index กำหนดไว้แล้วให้ใช้ตามนั้น หากไม่มีให้นับอัตโนมัติ 1, 2, 3, 4
        if len(item) >= 3 and item[2] is not None:
            sub_idx = item[2]
        else:
            key = (c_name, s_name)
            type_counts[key] = type_counts.get(key, 0) + 1
            sub_idx = type_counts[key]

        c_cfg = COLOR_CONFIGS.get(c_name, {
            "draw_color": (0, 255, 0),
            "led_rgb": (255, 255, 255),
            "name_th": c_name
        })
        s_th = SHAPE_TH_MAP.get(s_name, s_name)
        seq.append({
            "color": c_name,
            "shape": s_name,
            "sub_index": sub_idx,
            "label": f"{c_name} {s_name} #{sub_idx}",
            "draw_color": c_cfg["draw_color"],
            "led_rgb": c_cfg["led_rgb"],
            "name_th": f"{s_th}{c_cfg['name_th']} (อันที่ {sub_idx} จากซ้าย)"
        })
    return seq


# ==============================================================================
# หน้าต่างกราฟิก GUI สำหรับเลือกลำดับเป้าหมาย (Sequence Selector Dialog)
# ==============================================================================
class TargetSelectorGUI:
    def __init__(self, initial_sequence=None, auto_fire=True, enable_fire=True):
        self.sequence_list = list(initial_sequence) if initial_sequence else []
        self.auto_fire = auto_fire
        self.enable_fire = enable_fire
        self.is_confirmed = False

        self.root = tk.Tk()
        self.root.title("🎯 เลือกลำดับเป้าหมายและยิงกระสุนจริง (RoboMaster Target Sequence UI)")
        self.root.geometry("880x740")
        self.root.resizable(False, False)
        self.root.configure(bg="#1E1E2E")

        self.auto_fire_var = tk.BooleanVar(value=self.auto_fire)
        self.enable_fire_var = tk.BooleanVar(value=self.enable_fire)

        self.build_ui()
        self.refresh_sequence_display()

    def build_ui(self):
        # 1. แถบหัวเรื่อง (Header)
        header_frame = tk.Frame(self.root, bg="#2A2A3E", padx=18, pady=12)
        header_frame.pack(fill="x")

        title_lbl = tk.Label(header_frame, text="🎯 เลือกลำดับเป้าหมาย & ยิงกระสุนเจลจริง (WATER_FIRE)",
                             font=("Segoe UI", 15, "bold"), fg="#00FFAA", bg="#2A2A3E")
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(header_frame, text="หากเจอเป้าหมายแบบเดียวกัน ระบบจะจำและแยกเป็นลำดับ #1, #2, #3, #4 จากซ้ายไปขวาอัตโนมัติ",
                           font=("Tahoma", 10), fg="#B0B0C8", bg="#2A2A3E")
        sub_lbl.pack(anchor="w", pady=(3, 0))

        # 2. พื้นที่หลัก: แบ่ง 2 คอลัมน์ (ซ้าย = ตารางเลือกเป้าหมาย, ขวา = รายการลำดับที่จะยิง)
        main_frame = tk.Frame(self.root, bg="#1E1E2E", padx=15, pady=8)
        main_frame.pack(fill="both", expand=True)

        # -----------------------------
        # ฝั่งซ้าย: ตารางเป้าหมายให้คลิกเลือกเพิ่ม
        # -----------------------------
        left_frame = tk.LabelFrame(main_frame, text=" 📋 คลิกเป้าหมายเพื่อเพิ่มลงในลำดับ (ซ้าย -> ขวา) ",
                                   font=("Tahoma", 10, "bold"), fg="#00E5FF", bg="#252538",
                                   padx=10, pady=10, relief="groove")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # หัวตารางรูปทรง
        tk.Label(left_frame, text="สีเป้าหมาย", font=("Tahoma", 9, "bold"),
                 fg="#FFAA00", bg="#252538", width=10).grid(row=0, column=0, pady=4)

        for col_idx, (s_id, s_th, s_icon) in enumerate(AVAILABLE_SHAPES, start=1):
            tk.Label(left_frame, text=f"{s_icon}\n{s_th}", font=("Tahoma", 9, "bold"),
                     fg="#EAEAEA", bg="#252538", width=10).grid(row=0, column=col_idx, padx=3, pady=4)

        # แถวสีทั้ง 4 สี
        for row_idx, (c_id, c_th, c_hex, _) in enumerate(AVAILABLE_COLORS, start=1):
            # ปุ่มเพิ่มทั้งสี
            row_btn = tk.Button(left_frame, text=f"● {c_th}", font=("Tahoma", 9, "bold"),
                                fg=c_hex, bg="#1A1A28", activebackground="#303045",
                                relief="solid", bd=1, width=10, cursor="hand2",
                                command=lambda c=c_id: self.add_all_of_color(c))
            row_btn.grid(row=row_idx, column=0, padx=3, pady=5, sticky="ew")

            # ปุ่มเพิ่มแต่ละคู่ (Color, Shape)
            for col_idx, (s_id, s_th, s_icon) in enumerate(AVAILABLE_SHAPES, start=1):
                btn_text = f"+ {s_th}\n{c_th}"
                btn = tk.Button(left_frame, text=btn_text, font=("Tahoma", 8, "bold"),
                                fg="#FFFFFF", bg="#32324A", activebackground=c_hex,
                                activeforeground="#000000", relief="raised", bd=1,
                                width=10, height=2, cursor="hand2",
                                command=lambda c=c_id, s=s_id: self.add_to_sequence(c, s))
                btn.grid(row=row_idx, column=col_idx, padx=3, pady=5)

        # แถบปุ่มเพิ่มแบบกลุ่ม
        quick_frame = tk.Frame(left_frame, bg="#252538", pady=8)
        quick_frame.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(8, 0))

        tk.Label(quick_frame, text="⚡ เพิ่มทั้งรูปทรง:", font=("Tahoma", 9, "bold"),
                 fg="#FFAA00", bg="#252538").pack(side="left", padx=5)

        for s_id, s_th, s_icon in AVAILABLE_SHAPES:
            tk.Button(quick_frame, text=f"{s_icon} {s_th}", font=("Tahoma", 8),
                      bg="#404060", fg="white", activebackground="#606085", relief="flat", cursor="hand2",
                      command=lambda s=s_id: self.add_all_of_shape(s)).pack(side="left", padx=3)

        # ปุ่มเลือกซ้ายไปขวาอัตโนมัติ
        auto_scan_frame = tk.Frame(left_frame, bg="#252538", pady=6)
        auto_scan_frame.grid(row=6, column=0, columnspan=5, sticky="ew", pady=(4, 0))

        tk.Button(auto_scan_frame, text="⏩ สแกนเรียงลำดับจากซ้ายไปขวาอัตโนมัติตามกล้อง (Auto Left-to-Right)",
                  font=("Tahoma", 9, "bold"), bg="#1B4F72", fg="#00FFAA", activebackground="#2980B9",
                  relief="flat", cursor="hand2", pady=5,
                  command=self.set_auto_left_to_right).pack(fill="x", padx=4)

        # -----------------------------
        # ฝั่งขวา: รายการลำดับเป้าหมายที่จะยิง (Ordered Sequence)
        # -----------------------------
        right_frame = tk.LabelFrame(main_frame, text=" 🎯 ลำดับเป้าหมายที่จะยิงกระสุนจริง ",
                                    font=("Tahoma", 10, "bold"), fg="#FFDD00", bg="#252538",
                                    padx=10, pady=10, relief="groove")
        right_frame.pack(side="right", fill="both", expand=True)

        list_container = tk.Frame(right_frame, bg="#1E1E2E")
        list_container.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")

        self.seq_listbox = tk.Listbox(list_container, font=("Segoe UI", 9, "bold"),
                                      bg="#181824", fg="#00FFAA", selectbackground="#2D68C4",
                                      selectforeground="#FFFFFF", relief="flat", bd=2,
                                      yscrollcommand=scrollbar.set, height=13)
        self.seq_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.seq_listbox.yview)

        # ปุ่มควบคุมลำดับใน Listbox
        seq_btn_frame = tk.Frame(right_frame, bg="#252538", pady=8)
        seq_btn_frame.pack(fill="x")

        tk.Button(seq_btn_frame, text="⬆️ เลื่อนขึ้น", font=("Tahoma", 9, "bold"),
                  bg="#3A3A55", fg="white", activebackground="#505075", relief="flat", cursor="hand2",
                  command=self.move_up).pack(side="left", padx=2, fill="x", expand=True)

        tk.Button(seq_btn_frame, text="⬇️ เลื่อนลง", font=("Tahoma", 9, "bold"),
                  bg="#3A3A55", fg="white", activebackground="#505075", relief="flat", cursor="hand2",
                  command=self.move_down).pack(side="left", padx=2, fill="x", expand=True)

        tk.Button(seq_btn_frame, text="❌ ลบรายการ", font=("Tahoma", 9, "bold"),
                  bg="#C0392B", fg="white", activebackground="#E74C3C", relief="flat", cursor="hand2",
                  command=self.remove_selected).pack(side="left", padx=2, fill="x", expand=True)

        tk.Button(seq_btn_frame, text="🗑️ ล้างทั้งหมด", font=("Tahoma", 9, "bold"),
                  bg="#7F8C8D", fg="white", activebackground="#95A5A6", relief="flat", cursor="hand2",
                  command=self.clear_sequence).pack(side="left", padx=2, fill="x", expand=True)

        # 3. ตัวเลือกการยิงกระสุนจริง
        opt_frame = tk.Frame(self.root, bg="#1E1E2E", padx=15, pady=4)
        opt_frame.pack(fill="x")

        tk.Checkbutton(opt_frame, text="🎯 ยิงอัตโนมัติเมื่อล็อกเป้านิ่ง (Auto-Fire Real Bullet)",
                       variable=self.auto_fire_var, font=("Tahoma", 9, "bold"),
                       fg="#00E5FF", bg="#1E1E2E", selectcolor="#203040", cursor="hand2").pack(side="left", padx=5)

        tk.Checkbutton(opt_frame, text="💥 เปิดระบบยิงกระสุนเจลจริง (WATER_FIRE ARMED)",
                       variable=self.enable_fire_var, font=("Tahoma", 9, "bold"),
                       fg="#FF5555", bg="#1E1E2E", selectcolor="#203040", cursor="hand2").pack(side="right", padx=5)

        # 4. สรุปผล
        summary_frame = tk.Frame(self.root, bg="#14141E", padx=15, pady=6)
        summary_frame.pack(fill="x", padx=15, pady=3)

        self.summary_lbl = tk.Label(summary_frame, text="", font=("Tahoma", 9),
                                    fg="#00FFAA", bg="#14141E", justify="left", wraplength=830)
        self.summary_lbl.pack(anchor="w")

        # 5. ปุ่มควบคุมด้านล่าง (Start & Cancel)
        btn_frame = tk.Frame(self.root, bg="#1E1E2E", pady=10, padx=15)
        btn_frame.pack(fill="x")

        start_btn = tk.Button(btn_frame, text="🚀 เริ่มต้นทำงานและเล็งยิงกระสุนจริง (START ROBOT & CAMERA)",
                              font=("Segoe UI", 12, "bold"), bg="#00C853", fg="white",
                              activebackground="#00E676", pady=8, cursor="hand2",
                              relief="flat", command=self.on_start)
        start_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        cancel_btn = tk.Button(btn_frame, text="ยกเลิก / ออก (Exit)", font=("Tahoma", 10),
                               bg="#555566", fg="white", activebackground="#777788",
                               pady=8, padx=15, cursor="hand2", relief="flat",
                               command=self.on_cancel)
        cancel_btn.pack(side="right")

    def add_to_sequence(self, color, shape):
        """เพิ่มคู่ (color, shape) ลงในลำดับต่อท้าย"""
        self.sequence_list.append((color, shape))
        self.refresh_sequence_display()

    def add_all_of_color(self, color):
        """เพิ่มทุกรูปทรงของสีนี้ลงในลำดับ"""
        for s_id, _, _ in AVAILABLE_SHAPES:
            self.sequence_list.append((color, s_id))
        self.refresh_sequence_display()

    def add_all_of_shape(self, shape):
        """เพิ่มทุกสีของรูปทรงนี้ลงในลำดับ"""
        for c_id, _, _, _ in AVAILABLE_COLORS:
            self.sequence_list.append((c_id, shape))
        self.refresh_sequence_display()

    def set_auto_left_to_right(self):
        """ล้างลำดับเพื่อให้ระบบใช้โหมดสแกนซ้ายไปขวาอัตโนมัติตามกล้อง"""
        self.sequence_list = []
        self.refresh_sequence_display()

    def move_up(self):
        """เลื่อนลำดับเป้าหมายขึ้น 1 ตำแหน่ง"""
        sel = self.seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx > 0:
            self.sequence_list[idx], self.sequence_list[idx - 1] = self.sequence_list[idx - 1], self.sequence_list[idx]
            self.refresh_sequence_display()
            self.seq_listbox.selection_set(idx - 1)

    def move_down(self):
        """เลื่อนลำดับเป้าหมายลง 1 ตำแหน่ง"""
        sel = self.seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.sequence_list) - 1:
            self.sequence_list[idx], self.sequence_list[idx + 1] = self.sequence_list[idx + 1], self.sequence_list[idx]
            self.refresh_sequence_display()
            self.seq_listbox.selection_set(idx + 1)

    def remove_selected(self):
        """ลบเป้าหมายที่เลือกออกจากลำดับ"""
        sel = self.seq_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.sequence_list[idx]
        self.refresh_sequence_display()
        if self.sequence_list:
            new_idx = min(idx, len(self.sequence_list) - 1)
            self.seq_listbox.selection_set(new_idx)

    def clear_sequence(self):
        """ล้างรายการลำดับทั้งหมด"""
        self.sequence_list = []
        self.refresh_sequence_display()

    def refresh_sequence_display(self):
        """
        อัปเดตการแสดงผลใน Listbox และแถบสรุป
        คำนวณลำดับ 1, 2, 3, 4 สำหรับเป้าหมายที่เหมือนกันจากซ้ายไปขวาอย่างชัดเจน
        """
        self.seq_listbox.delete(0, tk.END)

        color_th_map = {c_id: c_th for c_id, c_th, _, _ in AVAILABLE_COLORS}
        shape_icon_map = {s_id: s_icon for s_id, _, s_icon in AVAILABLE_SHAPES}

        type_counts = {}
        items_summary = []

        for idx, item in enumerate(self.sequence_list, start=1):
            color, shape = item[0], item[1]
            key = (color, shape)
            type_counts[key] = type_counts.get(key, 0) + 1
            sub_num = type_counts[key]

            c_th = color_th_map.get(color, color)
            s_th = SHAPE_TH_MAP.get(shape, shape)
            s_icon = shape_icon_map.get(shape, "🎯")

            item_text = f"  #{idx:02d} : {s_icon} {c_th} - {s_th} (อันที่ {sub_num} จากซ้าย) [{color} {shape} #{sub_num}]"
            self.seq_listbox.insert(tk.END, item_text)
            items_summary.append(f"#{idx}:{c_th}_{s_th}#{sub_num}")

        if self.sequence_list:
            items_desc = " -> ".join(items_summary)
            text = f"📌 กำหนดลำดับการยิง {len(self.sequence_list)} ขั้นตอน: {items_desc}"
        else:
            text = "⏩ โหมดสแกนซ้ายไปขวาอัตโนมัติ (Auto Left-to-Right Scan): กล้องจะตรวจจับเป้าหมายทั้งหมดและเรียงลำดับ 1 2 3 4 จากซ้ายไปขวาให้เอง"
        self.summary_lbl.config(text=text)

    def on_start(self):
        # บันทึก sub_index 1, 2, 3, 4 ให้กับเป้าหมายที่เหมือนกันอย่างชัดเจน
        type_counts = {}
        final_list = []
        for item in self.sequence_list:
            c, s = item[0], item[1]
            key = (c, s)
            type_counts[key] = type_counts.get(key, 0) + 1
            final_list.append((c, s, type_counts[key]))
        self.sequence_list = final_list

        self.auto_fire = self.auto_fire_var.get()
        self.enable_fire = self.enable_fire_var.get()
        self.is_confirmed = True
        self.root.destroy()

    def on_cancel(self):
        self.is_confirmed = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.sequence_list, self.auto_fire, self.enable_fire, self.is_confirmed


def open_gui_dialog(current_sequence=None, auto_fire=True, enable_fire=True):
    """เปิดหน้าต่าง GUI เพื่อเลือกลำดับเป้าหมาย"""
    gui = TargetSelectorGUI(initial_sequence=current_sequence, auto_fire=auto_fire, enable_fire=enable_fire)
    return gui.run()


def print_sequence_status(seq_pairs):
    """แสดงลำดับเป้าหมายใน Terminal"""
    if seq_pairs:
        print(f"\n🎯 [TARGET SEQUENCE] กำหนดไว้ทั้งหมด {len(seq_pairs)} ขั้นตอน:")
        for idx, item in enumerate(seq_pairs, start=1):
            c, s = item[0], item[1]
            sub = item[2] if len(item) > 2 else 1
            c_th = COLOR_CONFIGS.get(c, {}).get("name_th", c)
            s_th = SHAPE_TH_MAP.get(s, s)
            print(f"   ลำดับ #{idx} : {c} {s} #{sub} ({s_th}{c_th} - อันที่ {sub} จากซ้าย)")
    else:
        print("\n🎯 [TARGET SEQUENCE]: โหมดตรวจจับและจัดลำดับจาก ซ้าย -> ขวา อัตโนมัติ (Auto Scan L->R)")


# ==============================================================================
# ฟังก์ชันหลักสำหรับการทำงานร่วมกับหุ่นยนต์ RoboMaster
# ==============================================================================
def main():
    global start_record_time, app_mode, learned_sequence, current_step
    global is_shooting, last_active_label, ENABLE_FIRE, AUTO_FIRE_ENABLED, COLOR_CONFIGS
    global lock_start_time, ordered_sequence

    print("\n" + "="*80)
    print("  🚀 RoboMaster Target System (GUI Target Sequencing & Real Water Fire)")
    print("="*80)

    # 1. เปิดหน้าต่าง GUI เพื่อให้ผู้ใช้กำหนดลำดับเป้าหมาย
    chosen_sequence, auto_fire, enable_fire, confirmed = open_gui_dialog(
        current_sequence=ordered_sequence,
        auto_fire=AUTO_FIRE_ENABLED,
        enable_fire=ENABLE_FIRE
    )

    if not confirmed:
        print(">> ผู้ใช้กดยกเลิกในหน้าต่าง UI -> ปิดโปรแกรม")
        return

    ordered_sequence = chosen_sequence if chosen_sequence else []
    AUTO_FIRE_ENABLED = auto_fire
    ENABLE_FIRE = enable_fire

    print_sequence_status(ordered_sequence)

    # หากผู้ใช้เลือกลำดับไว้ ให้สร้าง learned_sequence ทันที
    if ordered_sequence:
        learned_sequence = build_sequence_from_pairs(ordered_sequence)
        current_step = 0

    # 2. เชื่อมต่อหุ่นยนต์ RoboMaster EP
    print("\nกำลังเชื่อมต่อกับหุ่นยนต์ RoboMaster EP...")
    ep_robot = robot.Robot()
    ep_robot.initialize(conn_type="ap")

    ep_gimbal = ep_robot.gimbal
    ep_blaster = ep_robot.blaster
    ep_led = ep_robot.led
    ep_camera = ep_robot.camera

    # ดับไฟเริ่มต้น
    ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)
    ep_blaster.set_led(brightness=0, effect=blaster.LED_OFF)

    print("เปิดกล้องและตั้งศูนย์ Gimbal...")
    ep_camera.start_video_stream(display=False)
    ep_gimbal.recenter().wait_for_completed()
    time.sleep(1)

    ep_gimbal.sub_angle(freq=20, callback=on_gimbal_angle)
    start_record_time = time.time()

    window_name = "RoboMaster Target System (GUI Sequence & Real Water Bullet)"
    cv2.namedWindow(window_name)

    is_locked = False
    cooldown_until = 0.0

    print("\n" + "="*80)
    print("  พร้อมทำงาน! ปุ่มลัดระหว่างทำงาน:")
    print("      - [SPACEBAR] : สั่งยิงกระสุนเจลจริงและข้ามไปเป้าถัดไป (Manual Override) / ล็อกลำดับ")
    print("      - [m]        : เปิดหน้าต่าง UI ปรับเปลี่ยนลำดับเป้าหมายใหม่ได้ตลอดเวลา")
    print("      - [a]        : สลับเปิด/ปิด โหมดยิงอัตโนมัติ (Auto-Fire ON/OFF)")
    print("      - [f]        : สลับเปิด/ปิด ระบบยิงกระสุนจริง (Safety Armed/Safe)")
    print("      - [n] / [p]  : ข้ามไปเป้าถัดไป / ย้อนกลับไปเป้าก่อนหน้า")
    print("      - [l]        : กลับไปโหมดสแกน/พรีวิวลำดับใหม่ (Re-Learn / Preview)")
    print("      - [r]        : รีเซ็ตศูนย์ Gimbal")
    print("      - [q]        : ออกจากโปรแกรมและบันทึกข้อมูล Time Response")
    print("="*80 + "\n")

    while True:
        img = ep_camera.read_cv2_image(strategy="newest", timeout=0.5)
        if img is None:
            continue

        h, w, _ = img.shape
        center_screen_x = w // 2
        center_screen_y = h // 2

        # วาด Crosshair กลางจอ
        cv2.line(img, (center_screen_x - 25, center_screen_y), (center_screen_x + 25, center_screen_y), (255, 255, 255), 1)
        cv2.line(img, (center_screen_x, center_screen_y - 25), (center_screen_x, center_screen_y + 25), (255, 255, 255), 1)

        # ค้นหาเป้าหมายทั้งหมดในเฟรมภาพ (เรียงซ้ายไปขวา พร้อมระบุ sub_index #1, #2, #3, #4)
        live_targets = detect_all_targets(img)

        # ==================================================
        # 1. โหมดเรียนรู้ / พรีวิวลำดับเป้าหมาย (LEARNING MODE)
        # ==================================================
        if app_mode == "LEARNING":
            ep_gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
            ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=150, b=255, effect=led.EFFECT_BREATH)

            # วาดเป้าหมายทุกตัวที่ตรวจพบในกล้อง พร้อมแสดงลำดับรวมและ sub_index (เช่น #1 Red Circle #1, #2 Red Circle #2)
            for idx, tgt in enumerate(live_targets):
                bx, by, bw, bh = tgt["bbox"]
                cx, cy = tgt["center"]
                draw_color = tgt["draw_color"]

                if tgt["shape"] == "Circle":
                    radius = max(bw, bh) // 2
                    cv2.circle(img, (cx, cy), radius, draw_color, 2)
                    cv2.circle(img, (cx, cy), 4, draw_color, -1)
                else:
                    cv2.rectangle(img, (bx, by), (bx + bw, by + bh), draw_color, 2)

                tag = f"#{idx+1} {tgt['color']} {tgt['shape']} #{tgt['sub_index']}"
                cv2.rectangle(img, (bx, max(15, by - 22)), (bx + len(tag) * 9 + 8, max(18, by)), (0, 0, 0), -1)
                cv2.putText(img, tag, (bx + 3, max(15, by - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

            # ส่วนหัว HUD
            cv2.putText(img, "=== [PREVIEW / LEARNING MODE] ===", (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)

            if ordered_sequence:
                seq_summary = "GUI Sequence: " + " -> ".join([f"#{i+1}:{item[0]}_{item[1][:4]}#{item[2] if len(item)>2 else 1}" for i, item in enumerate(ordered_sequence)])
            else:
                seq_summary = "Auto Scan (L->R): " + (" -> ".join([f"#{i+1}:{t['color']}_{t['shape'][:4]}#{t['sub_index']}" for i, t in enumerate(live_targets)]) if live_targets else "Waiting for targets in camera...")
            cv2.putText(img, seq_summary, (15, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)

            # แสดงประเภทอาวุธ
            cv2.putText(img, "Weapon: [REAL WATER GEL BULLET (WATER_FIRE) ONLY]", (15, 84),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 100), 1)

            # แถบล่าง
            overlay = img.copy()
            cv2.rectangle(overlay, (10, h - 85), (w - 10, h - 10), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.8, img, 0.2, 0, img)

            if ordered_sequence or len(live_targets) > 0:
                prompt_str = "Press [SPACEBAR] to LOCK & START REAL WATER BULLET TRACKING"
                cv2.putText(img, prompt_str, (20, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            else:
                prompt_str = "Waiting for targets... Place targets in view or press [M] to open Sequence UI."
                cv2.putText(img, prompt_str, (20, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (180, 180, 180), 1)

            controls_guide = "[M]: Open Sequence UI | [SPACE]: Lock & Track | [R]: Recenter | [Q]: Quit"
            cv2.putText(img, controls_guide, (20, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)

            cv2.imshow(window_name, img)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('q'):
                print("ผู้ใช้สั่งหยุดโปรแกรม")
                break
            elif key == ord('r'):
                ep_gimbal.recenter().wait_for_completed()
            elif key in (ord('m'), ord('M')):
                print("\n>> [GUI] เปิดหน้าต่าง UI เลือกลำดับเป้าหมาย...")
                c_seq, a_fire, e_fire, ok = open_gui_dialog(
                    current_sequence=ordered_sequence,
                    auto_fire=AUTO_FIRE_ENABLED,
                    enable_fire=ENABLE_FIRE
                )
                if ok:
                    ordered_sequence = c_seq if c_seq else []
                    AUTO_FIRE_ENABLED = a_fire
                    ENABLE_FIRE = e_fire
                    print_sequence_status(ordered_sequence)
                    if ordered_sequence:
                        learned_sequence = build_sequence_from_pairs(ordered_sequence)
                        current_step = 0
            elif key in (ord(' '), 13):
                # หากกำหนดลำดับไว้จาก GUI แล้ว ให้ใช้ลำดับนั้น
                if ordered_sequence:
                    learned_sequence = build_sequence_from_pairs(ordered_sequence)
                elif len(live_targets) > 0:
                    # หากไม่ได้กำหนดลำดับ ให้ใช้เป้าหมายที่ตรวจพบเรียงจากซ้ายไปขวาพร้อม sub_index
                    learned_sequence = [
                        {
                            "color": t["color"],
                            "shape": t["shape"],
                            "sub_index": t["sub_index"],
                            "label": t["label"],
                            "draw_color": t["draw_color"],
                            "led_rgb": t["led_rgb"],
                            "name_th": t["name_th"]
                        }
                        for t in live_targets
                    ]

                if len(learned_sequence) > 0:
                    current_step = 0
                    app_mode = "TRACKING"
                    lock_start_time = None
                    ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=255, b=0, effect=led.EFFECT_ON)
                    time.sleep(0.4)
                    ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)

                    print("\n" + "="*70)
                    print(f"🎯 [TARGETS LOCKED!] บันทึกลำดับ {len(learned_sequence)} เป้าหมาย เรียบร้อยแล้ว:")
                    for i, t in enumerate(learned_sequence):
                        sub_str = f" (อันที่ {t.get('sub_index', 1)} จากซ้าย)"
                        print(f"   ลำดับ #{i+1} : {t['color']} {t['shape']} #{t.get('sub_index', 1)}{sub_str}")
                    print("เริ่มเข้าสู่ระบบเล็งและยิงกระสุนเจลจริงอัตโนมัติ (AUTO WATER BULLET TRACKING)!")
                    print("="*70 + "\n")
                else:
                    print(">> ยังไม่มีเป้าหมายในลำดับ กรุณากด [M] เพื่อเลือกลำดับ หรือวางเป้าหมายหน้ากล้อง")

            continue

        # ==================================================
        # 2. โหมดเล็งและยิงกระสุนจริงตามลำดับ (TRACKING MODE)
        # ==================================================
        if len(learned_sequence) == 0:
            app_mode = "LEARNING"
            continue

        active_step_tgt = learned_sequence[current_step]
        req_sub_idx = active_step_tgt.get("sub_index", 1)
        last_active_label = f"Step{current_step+1}_{active_step_tgt['color']}_{active_step_tgt['shape']}_#{req_sub_idx}"

        # ค้นหาเป้าหมายทั้งหมดใน live_targets ที่ตรงกับสีและรูปทรงของ Step ปัจจุบัน
        matched_targets = []
        for t in live_targets:
            if t["color"] != active_step_tgt["color"]:
                continue
            t_shape = t["shape"]
            req_shape = active_step_tgt["shape"]
            if t_shape == req_shape or (req_shape == "Rectangle" and t_shape in ("Rect_H", "Rect_V", "Square")):
                matched_targets.append(t)

        # เรียงลำดับเป้าหมายที่สีและรูปทรงตรงกันจาก "ซ้ายไปขวา" ตามพิกัด X
        matched_targets.sort(key=lambda t: t["center"][0])

        target_to_aim = None
        if matched_targets:
            # 1. ค้นหาเป้าหมายที่มี sub_index ตรงกับที่ต้องการ (ลำดับ 1, 2, 3, 4 จากซ้ายไปขวา)
            for t in matched_targets:
                if t.get("sub_index", 1) == req_sub_idx:
                    target_to_aim = t
                    break
            # 2. Fallback หากตรวจพบไม่ครบตามจำนวน sub_index (เช่น ตัวทางซ้ายหลุดเฟรมไปแล้ว)
            if target_to_aim is None:
                target_idx = max(0, min(req_sub_idx - 1, len(matched_targets) - 1))
                target_to_aim = matched_targets[target_idx]

        # วาดเป้าหมายทั้งหมดในกล้อง
        for t in live_targets:
            bx, by, bw, bh = t["bbox"]
            is_current = (target_to_aim is not None and t is target_to_aim)
            box_col = (0, 255, 255) if is_current else (70, 70, 70)

            if t["shape"] == "Circle":
                cv2.circle(img, t["center"], max(bw, bh) // 2, box_col, 2 if is_current else 1)
            else:
                cv2.rectangle(img, (bx, by), (bx + bw, by + bh), box_col, 2 if is_current else 1)

            # แสดงป้ายชื่อและลำดับย่อยชัดเจน เช่น "Red Circle #1", "Red Circle #2"
            label_text = f"{t['color']} {t['shape']} #{t.get('sub_index', 1)}"
            cv2.putText(img, label_text, (bx, max(15, by - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, box_col, 1)

        in_cooldown = time.time() < cooldown_until
        lock_progress = 0.0

        if target_to_aim is not None and not in_cooldown and not is_shooting:
            norm_x = target_to_aim["norm_x"]
            norm_y = target_to_aim["norm_y"]
            cx, cy = target_to_aim["center"]

            # วาดวงกลมเล็งเป้า
            cv2.circle(img, (cx, cy), 6, (0, 0, 255), -1)
            cv2.circle(img, (cx, cy), 14, (0, 255, 255), 2)
            cv2.line(img, (center_screen_x, center_screen_y), (cx, cy), (0, 255, 255), 1)

            err_x = norm_x - 0.5
            err_y = 0.5 - norm_y

            is_centered = (abs(err_x) < LOCK_TOLERANCE_X) and (abs(err_y) < LOCK_TOLERANCE_Y)

            if is_centered:
                ep_gimbal.drive_speed(pitch_speed=0, yaw_speed=0)

                if lock_start_time is None:
                    lock_start_time = time.time()

                hold_duration = time.time() - lock_start_time
                lock_progress = min(1.0, hold_duration / LOCK_HOLD_TIME)

                if not is_locked:
                    is_locked = True
                    r_c, g_c, b_c = active_step_tgt["led_rgb"]
                    ep_led.set_led(comp=led.COMP_TOP_ALL, r=r_c, g=g_c, b=b_c, effect=led.EFFECT_ON)

                # ยิงกระสุนเจลจริงอัตโนมัติเมื่อนิ่งสมบูรณ์
                if AUTO_FIRE_ENABLED and hold_duration >= LOCK_HOLD_TIME:
                    print(f"\n[🎯 AUTO-LOCKED!] ล็อกเป้าหมายขั้นที่ #{current_step+1} [{active_step_tgt['color']} {active_step_tgt['shape']} #{req_sub_idx}] (อันที่ {req_sub_idx} จากซ้าย) นิ่งเข้ากึ่งกลาง -> ยิงกระสุนเจลจริงอัตโนมัติ!")
                    shoot_and_advance_step(ep_blaster, ep_led, auto=True)
                    is_locked = False
                    lock_start_time = None
                    cooldown_until = time.time() + AUTO_FIRE_COOLDOWN
            else:
                lock_start_time = None
                lock_progress = 0.0

                if is_locked:
                    ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)
                is_locked = False

                yaw_speed = pid_yaw.compute(err_x)
                pitch_speed = pid_pitch.compute(err_y)
                ep_gimbal.drive_speed(pitch_speed=pitch_speed, yaw_speed=yaw_speed)
        else:
            lock_start_time = None
            lock_progress = 0.0
            if is_locked:
                ep_blaster.set_led(brightness=0, effect=blaster.LED_OFF)
                ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)
            is_locked = False
            if not is_shooting:
                ep_gimbal.drive_speed(pitch_speed=0, yaw_speed=0)

        # HUD แสดงผลโหมด TRACKING
        header_text = f"Step ({current_step+1}/{len(learned_sequence)}): [{active_step_tgt['color']} {active_step_tgt['shape']} #{req_sub_idx}] (Target #{req_sub_idx} L->R) | Yaw: {current_yaw_angle:.1f} Pitch: {current_pitch_angle:.1f}"
        cv2.putText(img, header_text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (0, 255, 0), 2)

        # แถบแสดงลำดับทั้งหมด
        seq_bar = "Sequence: " + " -> ".join([
            f"[#{i+1} {s['color']} {s['shape'][:4]}#{s.get('sub_index', 1)}]" if i == current_step
            else f"#{i+1} {s['color']} {s['shape'][:4]}#{s.get('sub_index', 1)}"
            for i, s in enumerate(learned_sequence)
        ])
        cv2.putText(img, seq_bar, (20, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 220, 100), 1)

        auto_mode_str = "AUTO-FIRE ON" if AUTO_FIRE_ENABLED else "AUTO-FIRE OFF (Manual)"
        fire_status_str = "WATER_FIRE ARMED" if ENABLE_FIRE else "FIRE DISABLED (Safe)"
        mode_color = (0, 255, 0) if ENABLE_FIRE else (0, 255, 255)
        cv2.putText(img, f"Mode: [{auto_mode_str}] | Weapon: [{fire_status_str}]",
                    (20, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.48, mode_color, 1)

        # กรอบ Lock Zone กึ่งกลางจอ
        zone_half_w = int(w * LOCK_TOLERANCE_X)
        zone_half_h = int(h * LOCK_TOLERANCE_Y)
        cv2.rectangle(img, (center_screen_x - zone_half_w, center_screen_y - zone_half_h),
                           (center_screen_x + zone_half_w, center_screen_y + zone_half_h),
                           (0, 255, 0) if is_locked else (80, 80, 80), 1)

        overlay = img.copy()
        cv2.rectangle(overlay, (10, h - 90), (w - 10, h - 10), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.75, img, 0.25, 0, img)

        if in_cooldown:
            cv2.putText(img, ">> [COOLDOWN] พักระบบยิงกระสุนและเตรียมพร้อมสำหรับเป้าถัดไป...", (20, h - 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 0), 2)
        elif is_locked and target_to_aim is not None:
            # หลอด Progress Bar นับถอยหลังยิงกระสุนจริง
            bar_w = 220
            bar_h = 16
            bar_x = 20
            bar_y = h - 68
            cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (80, 80, 80), 1)
            fill_w = int(bar_w * lock_progress)
            fill_color = (0, 0, 255) if lock_progress >= 0.95 else (0, 255, 255)
            cv2.rectangle(img, (bar_x + 1, bar_y + 1), (bar_x + fill_w, bar_y + bar_h - 1), fill_color, -1)
            pct_text = f"LOCKING: {int(lock_progress * 100)}%"
            cv2.putText(img, pct_text, (bar_x + bar_w + 12, bar_y + 13),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.50, fill_color, 2)
            cv2.putText(img, f">> AUTO-WATER BULLET (#{req_sub_idx} L->R)! [M]: UI | [SPACE]: Force Shoot | [L]: Re-learn",
                        (20, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 255, 255), 1)
        elif target_to_aim is not None:
            cv2.putText(img, f"Tracking Step {current_step+1}: {active_step_tgt['color']} {active_step_tgt['shape']} #{req_sub_idx} (Target #{req_sub_idx} from Left)...",
                        (20, h - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 2)
            cv2.putText(img, "[A]: Auto-Fire | [F]: Safety | [N]: Next | [P]: Prev | [L]: Re-Learn | [M]: Sequence UI",
                        (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (180, 180, 180), 1)
        else:
            cv2.putText(img, f"Searching for Step {current_step+1}: {active_step_tgt['color']} {active_step_tgt['shape']} #{req_sub_idx} (Target #{req_sub_idx} from Left)...",
                        (20, h - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (160, 160, 160), 2)
            cv2.putText(img, "[N]: Next | [P]: Prev | [L]: Re-learn | [R]: Recenter | [M]: Open Sequence UI",
                        (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (180, 180, 180), 1)

        cv2.imshow(window_name, img)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("ผู้ใช้กด 'q' เพื่อหยุดการทำงาน")
            break
        elif key in (ord('a'), ord('A')):
            AUTO_FIRE_ENABLED = not AUTO_FIRE_ENABLED
            print(f">> [TOGGLE] สลับโหมดยิงอัตโนมัติ: {'เปิด (ON)' if AUTO_FIRE_ENABLED else 'ปิด (OFF)'}")
        elif key in (ord('f'), ord('F')):
            ENABLE_FIRE = not ENABLE_FIRE
            print(f">> [SAFETY] สลับสถานะการยิงกระสุนจริง: {'เปิด (ARMED)' if ENABLE_FIRE else 'ปิด (SAFE)'}")
        elif key == ord('r'):
            ep_gimbal.recenter().wait_for_completed()
            pid_yaw.reset()
            pid_pitch.reset()
            lock_start_time = None
        elif key in (ord('m'), ord('M')):
            print("\n>> [GUI] เปิดหน้าต่าง UI ปรับลำดับเป้าหมาย...")
            c_seq, a_fire, e_fire, ok = open_gui_dialog(
                current_sequence=ordered_sequence,
                auto_fire=AUTO_FIRE_ENABLED,
                enable_fire=ENABLE_FIRE
            )
            if ok:
                ordered_sequence = c_seq if c_seq else []
                AUTO_FIRE_ENABLED = a_fire
                ENABLE_FIRE = e_fire
                print_sequence_status(ordered_sequence)

                if ordered_sequence:
                    learned_sequence = build_sequence_from_pairs(ordered_sequence)
                    current_step = 0
                else:
                    app_mode = "LEARNING"

                is_locked = False
                lock_start_time = None
                ep_blaster.set_led(brightness=0, effect=blaster.LED_OFF)
                ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)
                pid_yaw.reset()
                pid_pitch.reset()
                continue
        elif key in (ord('l'), ord('L')):
            print("\n>> [RE-LEARN] สั่งกลับเข้าสู่โหมดสแกน/พรีวิวลำดับใหม่...")
            app_mode = "LEARNING"
            is_locked = False
            lock_start_time = None
            ep_blaster.set_led(brightness=0, effect=blaster.LED_OFF)
            ep_led.set_led(comp=led.COMP_TOP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)
            pid_yaw.reset()
            pid_pitch.reset()
            continue
        elif key in (ord('n'), ord('N'), ord('s'), ord('S'), 9):
            current_step = (current_step + 1) % len(learned_sequence)
            next_tgt = learned_sequence[current_step]
            next_sub = next_tgt.get('sub_index', 1)
            print(f">> [NEXT] ข้ามไปเป้า #{current_step+1} [{next_tgt['color']} {next_tgt['shape']} #{next_sub}]")
            is_locked = False
            lock_start_time = None
            pid_yaw.reset()
            pid_pitch.reset()
            cooldown_until = time.time() + 0.4
        elif key in (ord('p'), ord('P')):
            current_step = (current_step - 1) % len(learned_sequence)
            prev_tgt = learned_sequence[current_step]
            prev_sub = prev_tgt.get('sub_index', 1)
            print(f">> [PREV] ย้อนไปเป้า #{current_step+1} [{prev_tgt['color']} {prev_tgt['shape']} #{prev_sub}]")
            is_locked = False
            lock_start_time = None
            pid_yaw.reset()
            pid_pitch.reset()
            cooldown_until = time.time() + 0.4
        elif ord('1') <= key <= ord('9'):
            sel_idx = key - ord('1')
            if sel_idx < len(learned_sequence):
                current_step = sel_idx
                st = learned_sequence[current_step]
                st_sub = st.get('sub_index', 1)
                print(f">> [SELECT] สั่งเลือกขั้นที่ #{current_step+1} [{st['color']} {st['shape']} #{st_sub}] โดยตรง")
                is_locked = False
                lock_start_time = None
                pid_yaw.reset()
                pid_pitch.reset()
                cooldown_until = time.time() + 0.4
        elif key in (ord(' '), 13, ord('y'), ord('Y')):
            if is_locked and target_to_aim is not None:
                shoot_and_advance_step(ep_blaster, ep_led, auto=False)
                is_locked = False
                lock_start_time = None
                cooldown_until = time.time() + AUTO_FIRE_COOLDOWN
            else:
                fire_water_bullet(ep_blaster, ep_led)
                lock_start_time = None
                cooldown_until = time.time() + 0.8

    # ปิดการทำงาน
    ep_gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
    ep_gimbal.unsub_angle()
    ep_camera.stop_video_stream()
    cv2.destroyAllWindows()
    ep_blaster.set_led(brightness=0, effect=blaster.LED_OFF)
    ep_led.set_led(comp=led.COMP_ALL, r=0, g=0, b=0, effect=led.EFFECT_OFF)
    ep_robot.close()

    os.makedirs("data", exist_ok=True)
    save_paths = [os.path.join("data", "sequence_auto_water_response.csv"), "sequence_auto_water_response.csv"]
    for p in save_paths:
        with open(p, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_sec", "pitch_angle", "yaw_angle", "target_step", "is_fired"])
            writer.writerows(data_log)

    print(f"\n>> บันทึกข้อมูล Time Response เรียบร้อยแล้ว: data/sequence_auto_water_response.csv (จำนวน {len(data_log)} บรรทัด)")


if __name__ == '__main__':
    main()
