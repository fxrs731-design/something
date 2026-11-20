DARK_F1_QSS = """
QMainWindow, QWidget {
    background-color: #090b10;
    color: #eef2f9;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}

QFrame#Sidebar {
    background-color: #0e1118;
    border-right: 1px solid #232b3e;
}

QFrame#Card, QFrame#DropZone {
    background-color: #141926;
    border: 1px solid #232b3e;
    border-radius: 10px;
}

QFrame#DropZone {
    border: 2px dashed #344160;
    background-color: #101420;
}

QLabel#PageTitle {
    font-size: 20px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#SectionTitle {
    font-size: 14px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#MutedText {
    color: #8b95ad;
    font-size: 12px;
}

QLabel#BadgeInstalled {
    background-color: rgba(16, 185, 129, 0.2);
    border: 1px solid #10b981;
    color: #6ee7b7;
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 700;
}

QLabel#BadgeWarning {
    background-color: rgba(245, 158, 11, 0.16);
    border: 1px solid #f59e0b;
    color: #fcd34d;
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}

QPushButton {
    background-color: #1c2335;
    border: 1px solid #2a344d;
    border-radius: 8px;
    padding: 8px 14px;
    color: #eef2f9;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #263047;
}

QPushButton#PrimaryBtn {
    background-color: #e10600;
    border-color: #e10600;
    color: #ffffff;
    font-weight: 700;
}

QPushButton#PrimaryBtn:hover {
    background-color: #ff2b24;
}

QPushButton#SuccessBtn {
    background-color: #065f46;
    border-color: #10b981;
    color: #d1fae5;
}

QPushButton#DangerBtn {
    background-color: rgba(225, 6, 0, 0.16);
    border-color: #e10600;
    color: #fca5a5;
}

QPushButton#NavBtn {
    background-color: transparent;
    border: 1px solid transparent;
    text-align: left;
    padding: 10px 12px;
    color: #8b95ad;
    border-radius: 8px;
}

QPushButton#NavBtn:hover {
    background-color: rgba(255, 255, 255, 0.04);
    color: #ffffff;
}

QPushButton#NavBtn[active="true"] {
    background-color: rgba(225, 6, 0, 0.16);
    border: 1px solid rgba(225, 6, 0, 0.45);
    color: #ffffff;
}

QLineEdit, QComboBox, QPlainTextEdit {
    background-color: #090b10;
    border: 1px solid #232b3e;
    border-radius: 8px;
    padding: 7px 10px;
    color: #eef2f9;
}

QProgressBar {
    background-color: #090b10;
    border: 1px solid #232b3e;
    border-radius: 6px;
    height: 10px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #e10600;
    border-radius: 5px;
}

QScrollArea {
    border: none;
    background-color: transparent;
}
"""
