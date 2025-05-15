import sqlite3
import os
from datetime import datetime
import boto3
from fpdf import FPDF
import matplotlib.pyplot as plt
import seaborn as sns  # <-- Directly import seaborn
from collections import Counter
import os
import matplotlib
from dotenv import load_dotenv
matplotlib.use('Agg')  # Ensures no GUI backend is used


load_dotenv()

# Configuration variables
AWS_ACCESS_KEY_ID= os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
bucket_name = os.getenv('bucket_name')


def create_bar_chart(labels, values, title, x_label, y_label, filename):
    """
    Creates a bar chart using Seaborn's 'darkgrid' style and saves it to 'filename'.
    """
    # Use Seaborn's built-in theme
    sns.set_theme(style='darkgrid')
    plt.figure(figsize=(8, 4))  # 8 inches wide, 4 inches tall

    # Create the bar plot
    plt.bar(labels, values, color='steelblue')

    # Set labels, title, etc.
    plt.title(title)
    plt.xlabel(x_label)
    plt.ylabel(y_label)

    # Rotate x-axis labels for clarity
    plt.xticks(rotation=45, ha='right')

    # Ensure layout is tight and nothing is cut off
    plt.tight_layout()

    # Save the figure
    plt.savefig(filename, bbox_inches='tight')
    plt.close()


class PDF(FPDF):
    def __init__(self):
        super().__init__()
        # Set margins and auto page break
        self.set_margins(10, 10, 10)
        self.set_auto_page_break(auto=True, margin=10)

    def ensure_space_for_chart(self, chart_height=100):
        """
        Checks if there is enough space left on the current page
        for a chart that is approximately 'chart_height' mm tall (plus some text).
        If not, adds a new page so the chart title and chart appear together.
        """
        current_y = self.get_y()
        bottom_limit = self.h - self.b_margin
        if (current_y + chart_height) > bottom_limit:
            self.add_page()


def generate_report(db_filename, reports_dir, device_id):
    conn = sqlite3.connect(db_filename)
    c = conn.cursor()

    # ---------------------------
    # Demographics from faces
    # ---------------------------
    c.execute("SELECT COUNT(*) FROM faces")
    total_count = c.fetchone()[0] or 0

    c.execute("SELECT age_range, gender FROM faces")
    demographics_data = c.fetchall()

    demographics = Counter()
    for age_range, gender_str in demographics_data:
        try:
            dominant_gender = gender_str
        except (SyntaxError, TypeError, ValueError):
            dominant_gender = "Unknown"
        demographics[f"{age_range} - {dominant_gender}"] += 1

    sorted_demographics = demographics.most_common()

    # ---------------------------
    # Create PDF
    # ---------------------------
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Times", style="", size=12)

    # Title of the report
    pdf.set_font("Times", style="B", size=16)
    pdf.cell(0, 10, txt="Demographics Report", ln=True, align='C')
    pdf.ln(5)

    # Subsection title
    pdf.set_font("Times", style="B", size=14)
    pdf.cell(0, 10, txt="Most Common Demographics", ln=True, align='L')
    pdf.ln(5)

    # Reset to normal font for body
    pdf.set_font("Times", size=12)
    for demo, count in sorted_demographics:
        percentage = (count / total_count) * 100 if total_count > 0 else 0
        pdf.cell(0, 8, txt=f"{demo}: {count} ({percentage:.2f}%)", ln=True)
    pdf.ln(5)

    # -----------------------------------------------------------
    # 1) Peak time graph (hourly) in 12-hour AM/PM format (last 7 days)
    # -----------------------------------------------------------
    c.execute("""
        SELECT timestamp
        FROM identifications
        WHERE timestamp >= datetime('now','-7 days')
    """)
    rows = c.fetchall()

    # Parse timestamps with Python
    timestamps = []
    for row in rows:
        ts_str = row[0]
        if ts_str:
            try:
                # If your DB format is YYYY-MM-DD HH:MM:SS
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                timestamps.append(dt)
            except ValueError:
                pass

    # 12-hour format counts
    hours_12 = [dt.strftime('%I %p') for dt in timestamps]
    hour_counts_12 = Counter(hours_12)

    if hour_counts_12:
        hour_labels_12 = [
            "12 AM", "01 AM", "02 AM", "03 AM", "04 AM", "05 AM",
            "06 AM", "07 AM", "08 AM", "09 AM", "10 AM", "11 AM",
            "12 PM", "01 PM", "02 PM", "03 PM", "04 PM", "05 PM",
            "06 PM", "07 PM", "08 PM", "09 PM", "10 PM", "11 PM"
        ]
        hour_values = [hour_counts_12.get(h, 0) for h in hour_labels_12]

        peak_times_path = os.path.join(reports_dir, 'peak_times.png')
        create_bar_chart(
            labels=hour_labels_12,
            values=hour_values,
            title='Peak Times (Last 7 Days)',
            x_label='Hour (12-hour format)',
            y_label='Identifications',
            filename=peak_times_path
        )

        pdf.ensure_space_for_chart(chart_height=110)
        pdf.set_font("Times", style="B", size=14)
        pdf.cell(0, 8, txt="Peak Times Graph (12-hour format, Last 7 Days)", ln=True, align='L')
        pdf.ln(3)
        pdf.image(peak_times_path, x=10, y=None, w=190)
        pdf.ln(5)

    # -----------------------------------------------------------
    # 2) Daily traffic graph (last 7 days)
    # -----------------------------------------------------------
    day_counts = Counter(dt.strftime('%Y-%m-%d') for dt in timestamps)
    if day_counts:
        sorted_days = sorted(day_counts.keys())
        day_values = [day_counts[day] for day in sorted_days]

        daily_traffic_path = os.path.join(reports_dir, 'daily_traffic.png')
        create_bar_chart(
            labels=sorted_days,
            values=day_values,
            title='Daily Traffic (Last 7 Days)',
            x_label='Date',
            y_label='Traffic Count',
            filename=daily_traffic_path
        )

        pdf.ensure_space_for_chart(chart_height=110)
        pdf.set_font("Times", style="B", size=14)
        pdf.cell(0, 8, txt="Daily Traffic Graph (Last 7 Days)", ln=True, align='L')
        pdf.ln(3)
        pdf.image(daily_traffic_path, x=10, y=None, w=190)
        pdf.ln(5)

    # -----------------------------------------------------------
    # 3) Peak days of the week (last 7 days)
    # -----------------------------------------------------------
    weekday_counts = Counter(dt.strftime('%a') for dt in timestamps)
    if weekday_counts:
        labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        day_values = [weekday_counts.get(day, 0) for day in labels]

        peak_days_path = os.path.join(reports_dir, 'peak_days.png')
        create_bar_chart(
            labels=labels,
            values=day_values,
            title='Peak Days of the Week (Last 7 Days)',
            x_label='Day of the Week',
            y_label='Identifications',
            filename=peak_days_path
        )

        pdf.ensure_space_for_chart(chart_height=110)
        pdf.set_font("Times", style="B", size=14)
        pdf.cell(0, 8, txt="Peak Days Graph (Last 7 Days)", ln=True, align='L')
        pdf.ln(3)
        pdf.image(peak_days_path, x=10, y=None, w=190)
        pdf.ln(5)

    # -----------------------------------------------------------
    # Save final PDF
    # -----------------------------------------------------------
    report_filename = f"report_{device_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    report_path = os.path.join(reports_dir, report_filename)
    pdf.output(report_path)
    print(f"Report saved as {report_path}")

    # Upload report to S3
    s3 = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )
    try:
        s3.upload_file(report_path, bucket_name, f"Reports/{report_filename}")
        print(f"Uploaded {report_path} to s3://{bucket_name}/Reports/{report_filename}")
    except Exception as e:
        print(f"Error uploading report to S3: {e}")

    conn.close()
