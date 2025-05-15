from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import os
import boto3
from PyPDF2 import PdfReader
from dotenv import load_dotenv
import mysql.connector
import pandas as pd
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# Load environment variables from .env file
load_dotenv()

# Get the API key from environment variables
API_KEY = os.getenv('API_KEY')

# Hardcoded AWS credentials
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID') # Replace with your actual access key ID
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')  # Replace with your actual secret access key

# Initialize AWS S3 client
s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

def fetch_all_pdfs_from_s3(bucket_name, prefix):
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    pdf_files = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.pdf')]

    pdf_contents = {}
    for file_key in pdf_files:
        local_path = os.path.basename(file_key)
        s3.download_file(bucket_name, file_key, local_path)
        pdf_contents[os.path.splitext(local_path)[0]] = extract_text_from_pdf(local_path)
        os.remove(local_path)  # Clean up local files after extraction
    return pdf_contents

def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text

# Database connection details
HOST = os.getenv('HOST')
USER = os.getenv('ROOT')
PASSWORD = os.getenv('PASSWORD')
DATABASE = "customer_data"
PORT = 3306

def fetch_customer_data():
    try:
        connection = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE,
            port=PORT
        )

        if connection.is_connected():
            cursor = connection.cursor()
            query = "SELECT customer_ID, company_name, target_audience, avg_price_per_ad FROM customers"
            cursor.execute(query)

            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            return pd.DataFrame(rows, columns=columns)

    except mysql.connector.Error as e:
        print(f"Error connecting to MySQL: {e}")
    finally:
        if connection.is_connected():
            connection.close()

customer_data = fetch_customer_data()

llm = ChatOpenAI(api_key=API_KEY, model_name="gpt-4o-mini")

machine_analysis_template = """
Demographic Report ID: {report_id}
Report Content: {report_content}

Customer Data:
- Company Name: {company_name}
- Target Audience: {target_audience}
- Average Price per Ad: {avg_price_per_ad}

Demographic Report ID: {report_id}
Report Content: {report_content}

Customer Data:
- Company Name: {company_name}
- Target Audience: {target_audience}
- Average Price per Ad: {avg_price_per_ad}

Context: Each vending machine generates a demographic report based on its location. 
Customers are assigned to the machine that best matches their target audience and maximizes profit.
If no close match is found, the customer is assigned to the machine that provides the next best alignment.

Question: Based on the demographic report, does this customer align with the needs of any specific machine, 
while maximizing profit? Provide reasoning and suggest a location (based on the report_id) for this customer 
if applicable, even if it is not a perfect fit. Always output a recommended location.
The reasoning should be a concise paragraph.
"""
prompt = PromptTemplate(
    template=machine_analysis_template,
    input_variables=["report_id", "report_content", "company_name", "target_audience", "avg_price_per_ad"]
)

def analyze_customer_with_llm(prompt, llm, input_values):
    return (prompt | llm).invoke(input_values)

def match_clients_to_machines(demographic_reports, customer_data):
    matches = []
    
    # Convert demographic_reports dict to a list of (report_id, content) 
    # for potential fallback use (e.g., the first or best numeric match)
    all_report_ids = list(demographic_reports.keys())
    
    for _, row in customer_data.iterrows():
        best_match = None
        best_reasoning = "No exact match found."
        
        for report_id, report_content in demographic_reports.items():
            input_values = {
                "report_id": report_id,
                "report_content": report_content,
                "company_name": row["company_name"],
                "target_audience": row["target_audience"],
                "avg_price_per_ad": row["avg_price_per_ad"]
            }

            analysis = analyze_customer_with_llm(prompt, llm, input_values)
            analysis_content = analysis.content if hasattr(analysis, "content") else str(analysis)

            # Check if the LLM says "ideal" or "recommended"
            if "ideal" in analysis_content.lower() or "recommended" in analysis_content.lower():
                best_match = f"Machine {report_id}"
                best_reasoning = analysis_content
                break  # If you want the first "ideal" machine, you can break here

        # Fallback if no "ideal" or "recommended" was found
        if not best_match:
            # 1) Optionally parse LLM output for a location reference
            # 2) Or just pick the first available location as a fallback

            # Let's pick the first location for fallback:
            fallback_report_id = all_report_ids[0]  # or any custom logic
            best_match = f"Machine {fallback_report_id}"
            best_reasoning += (
                "\nNo explicit ideal match in the LLM response. "
                f"Assigning fallback: Machine {fallback_report_id}"
            )

        matches.append({
            "Customer ID": row["customer_ID"],
            "Company Name": row["company_name"],
            "Average Ad Cost": row["avg_price_per_ad"],
            "Suggested Location": best_match,
            "Reasoning": best_reasoning
        })

    return matches

def save_reports_to_pdf(matches, file_name="ad_suggestion_report.pdf"):

    # Create a style sheet and pick one for normal text
    styles = getSampleStyleSheet()  # <-- ADDED
    normal_style = styles['Normal']  # <-- ADDED
    # If you want to tweak word wrapping, you could do:
    # normal_style.wordWrap = 'CJK'  # or 'LTR'

    # (Optional) Remove or comment out the manual wrap function:
    # def wrap_text_in_cell(text, max_width, font, font_size):
    #     # manual wrapping logic
    #     return text  # or commented out entirely

    pdf_path = os.path.join("reports", file_name)
    os.makedirs("reports", exist_ok=True)

    doc = SimpleDocTemplate(
        pdf_path, 
        pagesize=letter, 
        leftMargin=30, 
        rightMargin=30, 
        topMargin=30, 
        bottomMargin=30
    )
    elements = []

    headers = ["Customer ID", "Company Name", "Average Ad Cost", "Suggested Location", "Reasoning"]
    column_widths = [60, 120, 80, 120, 200]
    header_font_size = 10
    body_font_size = 9

    # Build the data for the table
    data = []
    
    # Convert headers to Paragraph objects for consistency
    header_paragraphs = [
        Paragraph(h, styles['Heading6']) for h in headers  # Using a smaller heading style
    ]
    data.append(header_paragraphs)

    # Build rows using Paragraph objects
    for match in matches:
        row = [
            Paragraph(str(match["Customer ID"]), normal_style),
            Paragraph(match["Company Name"], normal_style),
            Paragraph(f"${match['Average Ad Cost']:.2f}", normal_style),
            Paragraph(match["Suggested Location"], normal_style),
            Paragraph(match["Reasoning"], normal_style),
        ]
        data.append(row)

    # If total width is more than page width, scale columns
    total_table_width = sum(column_widths)
    max_page_width = letter[0] - 60  # page width minus left/right margin
    if total_table_width > max_page_width:
        scaling_factor = max_page_width / total_table_width
        column_widths = [width * scaling_factor for width in column_widths]

    table = Table(data, colWidths=column_widths, repeatRows=1)

    # Table style
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), header_font_size),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), body_font_size),
        ('VALIGN', (0, 1), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        # Remove or comment out WORDWRAP if using Paragraph:
        # ('WORDWRAP', (0, 1), (-1, -1), True),
    ]))

    elements.append(table)
    doc.build(elements)
    print(f"PDF report saved at {pdf_path}")
    return pdf_path

def upload_pdf_to_s3(local_file_path, bucket_name, s3_key):
    try:
        s3.upload_file(local_file_path, bucket_name, s3_key)
        print(f"Successfully uploaded {local_file_path} to s3://{bucket_name}/{s3_key}")
    except Exception as e:
        print(f"Error uploading file to S3: {e}")

if __name__ == "__main__":
    pdf_reports = fetch_all_pdfs_from_s3("testbucket8th", "Reports/")
    if customer_data is not None and not customer_data.empty:
        if pdf_reports:
            matches = match_clients_to_machines(pdf_reports, customer_data)
            pdf_file_path = save_reports_to_pdf(matches)
            upload_pdf_to_s3(pdf_file_path, "testbucket8th", "ad_suggestion_reports/ad_suggestion_report.pdf")
        else:
            print("No PDF reports found.")
    else:
        print("No customer data found or query failed.")
