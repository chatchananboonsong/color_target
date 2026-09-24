## สมาชิกกลุ่ม
1. 6810110066 นาย ซัซวาลย์ บินสะอิ
2. 6810110055 นาย ชัชนันท์ บุญส่ง
3. 6810110324 นาย วิญญู สิงห์สาธร
4. 6810110448 นาย จิระธาดา พัดบุรี

# 🎯 RoboMaster Color & Shape Target System (Infrared & PID Tracking)

ระบบตรวจจับวัตถุสีและรูปทรง (ทรงกลม / สี่เหลี่ยม), จัดลำดับเป้าหมายจากซ้ายไปขวา, ควบคุม Gimbal ด้วย PID Controller และเล็งยิงเป้าหมายอัตโนมัติด้วยสัญญาณอินฟราเรด (Infrared Blaster) พร้อมระบบบันทึกและพล็อตกราฟ Time Response

---

## 📁 โครงสร้างไฟล์ในโปรเจกต์ (Project Structure)

```text
color_detect/
│
├── 🚀 โปรแกรมหลัก (Main Programs)
│   ├── color_target_gui.py             # ระบบเล็งเป้าหมายพร้อมหน้าต่างกราฟิก UI (เลือกได้หลายแบบพร้อมกัน)
│   └── hsv_color_tuner.py              # เครื่องมือจูนค่าสี HSV แบบเรียลไทม์ผ่าน Trackbar
│
├── 📊 เครื่องมือวิเคราะห์ผล (Analysis & Visualization)
│   └── plot_shooting_response.py       # สคริปต์สร้างกราฟวิเคราะห์มุมกิมบอลและจังหวะการยิง
│
├── 📂 โฟลเดอร์ข้อมูลและผลลัพธ์ (Data & Results)
│   ├── data/                           # เก็บไฟล์ข้อมูล Time Response (CSV)
│   │   └── sequence_auto_infrared_response.csv
│   └── plots/                          # เก็บไฟล์ภาพกราฟผลการทดสอบ (PNG)
│       └── shooting_response_plot.png
│
├── ⚙️ ไฟล์คอนฟิก (Configuration)
│   └── hsv_config.json                 # ค่าช่วงสี HSV (แดง, เขียว, น้ำเงิน, เหลือง)
│
└── 📖 เอกสารกำกับ (Documentation)
    └── README.md                       # คู่มือการใช้งานและคำอธิบายโครงสร้างโปรเจกต์
```

---

## 🛠️ รายละเอียดของแต่ละไฟล์

| ชื่อไฟล์ | หน้าที่ / การทำงาน |
| **`color_target_gui.py`** | **ระบบยิงอินฟราเรดพร้อมหน้าต่าง UI (แนะนำ)**: มีหน้าต่างกราฟิก UI สวยงาม แสดงตาราง Checkbox 4x4 (16 รูปแบบเป้าหมาย: ทรงกลม, สี่เหลี่ยมจัตุรัส, ผืนผ้านอน, ผืนผ้าตั้ง) ให้คลิกเลือกสีและรูปทรงหลายแบบพร้อมกันได้อย่างสะดวก สามารถเปิด UI สลับเป้าหมายใหม่ได้ตลอดเวลาด้วยปุ่ม `[M]` |
| **`plot_shooting_response.py`** | **วิเคราะห์และวาดกราฟ**: อ่านข้อมูล CSV แสดงมุม Yaw/Pitch, PID Settling Time, จังหวะยิงกระสุน (Shot #1, #2, #3), และเซฟกราฟความละเอียดสูงลง `plots/shooting_response_plot.png` |
| **`hsv_color_tuner.py`** | **ปรับแต่งสี HSV**: เปิดกล้องหุ่นยนต์พร้อม Trackbar ปรับขอบเขตสี (Lower / Upper) และบันทึกลง `hsv_config.json` |
| **`hsv_config.json`** | ตารางค่าสี HSV ของ Red, Green, Blue, Yellow และรหัสสีไฟ LED ประจำเป้าหมาย |

---

## 🚀 วิธีการใช้งานโปรแกรม

### 1) ตั้งค่า Python environment

เปิด PowerShell หรือ Command Prompt ในโฟลเดอร์โปรเจคแล้วทำตามขั้นตอนนี้

```powershell
py -3.8 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

หากใช้ Git Bash หรือ bash อื่น อาจใช้คำสั่ง:

```bash
python3.8 -m venv .venv
source .venv/bin/activate
```

### 2) ติดตั้ง dependency

```powershell
cd .\color_target\
```

```powershell
pip install robomaster
pip install matplotlib
```