import os
import re
import glob
import pdfplumber
import pandas as pd
from PyPDF2 import PdfReader
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash
from werkzeug.utils import secure_filename

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with a secure secret key

# Configure upload folder and allowed extensions
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'
ALLOWED_EXTENSIONS = {'pdf'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB limit per upload

# Ensure upload and processed directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Existing processing functions
def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ''
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted
    return text

def extract_fonts_from_pdf(pdf_path):
    font_info = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract font styles and sizes
            for char in page.chars:
                font_info.append({
                    'fontname': char['fontname'],
                    'size': char['size']
                })
    
    # Create sets to get unique font names and sizes
    unique_fonts = set(info['fontname'] for info in font_info)
    unique_sizes = set(info['size'] for info in font_info)
    
    return ', '.join(unique_fonts), ', '.join(map(str, unique_sizes))

def extract_invoice_details(text):
    details = {}

    vendor_match = re.search(r'(\b[A-Z][\w\s&.,]+(?:Electronics|Pvt\.|Ltd)\b)', text)
    details['Company Name'] = vendor_match.group(1).strip().split('\n')[-1]  if vendor_match else 'N/A'

    vendor_address_match = re.search(r'(\b(?:Malad|Mumbai|Delhi|Road|Area|Street|Industrial)\b.*?)(Tel|Mob)', text, re.DOTALL)
    details['Company Address'] = vendor_address_match.group(1).strip() if vendor_address_match else 'N/A'

    vendor_contact_match = re.search(r'(Tel\. No\.|Mob\. No\.|Contact No\.)\s*:\s*(\d{10})', text)
    details['Company Contact'] = vendor_contact_match.group(2).strip() if vendor_contact_match else 'N/A'

    customer_match = re.search(r'M/s\.\s+([A-Za-z\s&.,]+),', text)
    details['Customer Name'] = customer_match.group(1).strip().split(",")[0] if customer_match else 'N/A'

    customer_address_match = re.search(r'M/s\.\s+([A-Za-z\s&.]+?),\s*(.+)', text)
    details['Customer Address'] = customer_address_match.group(2).strip() if customer_address_match else 'N/A'

    gstin_match = re.search(r'GSTIN No\.\s*(\w+)', text)
    details['Customer GSTIN'] = gstin_match.group(0).strip().split(".")[-1].strip() if gstin_match else 'N/A'

    invoice_match = re.search(r'Invoice\s*No\.\s*(INV\s*-\s*\d+\s*\d+)', text)
    details['Invoice Number'] = invoice_match.group(1).strip() if invoice_match else 'N/A'

    items = re.findall(r'([A-Za-z0-9\s.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)', text)

    for i, item in enumerate(items, start=1):
        item_name = item[0].strip()
        hsn = item[1]
        quantity = item[2]
        rate = item[3]
        total_price = item[4]
        
        details[f'Item_{i}'] = f'{item_name}, HSN: {hsn}, Quantity: {quantity}, Rate: {rate}, Price: {total_price}'

    before_tax_match = re.search(r'Total\s+(\d+)', text)
    details['Charges_Before_Tax'] = before_tax_match.group(1).strip() if before_tax_match else 'N/A'

    return details

def process_pdfs(pdf_files):
    df = pd.DataFrame(columns=['Company Name', 'Company Address', 'Company Contact', 'Customer GSTIN',
                               'Customer Name', 'Customer Address', 'Invoice Number',
                               'Item_1', 'Item_2', 'Item_3', 'Charges_Before_Tax', 'Font_Style'])

    issues = []

    for pdf in pdf_files:
        text = extract_text_from_pdf(pdf)
        details = extract_invoice_details(text)

        font_styles, font_sizes = extract_fonts_from_pdf(pdf)
        details['Font_Style'] = font_styles

        temp_df = pd.DataFrame([details])
        df = pd.concat([df, temp_df], ignore_index=True)

    items = []
    for details in df['Item_1']:
        lines = details.strip().split('\n')[3:]  # Skip the first three lines
        temp_item = []
        
        item_pattern = re.compile(r"^(.*), HSN:\s*(\d+), Quantity:\s*(\d+), Rate:\s*(\d+), Price:\s*([\d.]+)$")
        
        for line in lines:
            match = item_pattern.match(line)
            if match:
                item_name, hsn, quantity, rate, price = match.groups()
                temp_item.append(f"{item_name.strip()}_{quantity}_{rate}")
            else:
                parts = line.split()
                if len(parts) >= 5:
                    item_name = ' '.join(parts[:-4])  
                    hsn = parts[-4]
                    quantity = parts[-3]
                    rate = parts[-2]
                    price = parts[-1]
                    temp_item.append(f"{item_name.strip()}_{quantity}_{rate}")
        items.append(temp_item)
    df['Items_Qty_Rate'] = items

    def find_rate_differences(df):
        grouped = df.groupby('Customer Name')
        issue_count = 1
        
        for name, group in grouped:
            # Convert string representation of lists to actual lists
            group['Items_Qty_Rate'] = group['Items_Qty_Rate'].apply(
                lambda x: eval(x) if isinstance(x, str) else x)
            
            items_dict = {}
            
            for index, row in group.iterrows():
                for item in row['Items_Qty_Rate']:
                    product, qty, rate = item.split('_')
                    key = f"{product}_{qty}"
                    
                    if key in items_dict:
                        if items_dict[key] != rate:
                            issues.append({
                                'Issues': f"Issue_{issue_count}",
                                'Invoice number': row['Invoice Number'],
                                'Item': product,
                                'Rate': rate,
                                'Font': 'NA'  # No font difference for rate issues
                            })
                            # Append the first entry of the previous rate
                            previous_invoice = group.loc[
                                group['Items_Qty_Rate'].apply(
                                    lambda x: any(f'{product}_{qty}' in i for i in x)
                                )].iloc[0]
                            issues.append({
                                'Issues': f"Issue_{issue_count}",
                                'Invoice number': previous_invoice['Invoice Number'],
                                'Item': product,
                                'Rate': items_dict[key],
                                'Font': 'NA'
                            })
                            issue_count += 1
                    else:
                        items_dict[key] = rate

    def find_font_differences(df):
        grouped = df.groupby('Company Name')
        issue_count = len(issues) // 2 + 1  # Continue issue count
        
        for name, group in grouped:
            fonts = set(group['Font_Style'].unique())
            if len(fonts) > 1:  # More than one font detected
                for font in fonts:
                    for index, row in group[group['Font_Style'] == font].iterrows():
                        issues.append({
                            'Issues': f"Issue_{issue_count}",
                            'Invoice number': row['Invoice Number'],
                            'Item': 'NA',  # No item difference for font issues
                            'Rate': 'NA',  # No rate difference for font issues
                            'Font': font
                        })
                issue_count += 1

    def find_phone_differences(df):
        grouped = df.groupby('Company Name')
        issue_count = len(issues) // 2 + 1  # Continue issue count
        
        for name, group in grouped:
            contacts = set(group['Company Contact'].unique())
            if len(contacts) > 1:  # More than one contact detected
                for contact in contacts:
                    for index, row in group[group['Company Contact'] == contact].iterrows():
                        issues.append({
                            'Issues': f"Issue_{issue_count}",
                            'Invoice number': row['Invoice Number'],
                            'Item': 'NA',  # No item difference for phone issues
                            'Rate': 'NA',  # No rate difference for phone issues
                            'Font': 'NA',  # No font difference for phone issues
                            'Phone Number': contact
                        })
                issue_count += 1

    find_rate_differences(df)
    find_font_differences(df)
    find_phone_differences(df)
    issues_df = pd.DataFrame(issues)

    return df, issues_df

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'files[]' not in request.files:
        flash('No file part in the request.')
        return redirect(request.url)
    
    files = request.files.getlist('files[]')
    
    if not files or files[0].filename == '':
        flash('No files selected for uploading.')
        return redirect(request.url)
    
    saved_files = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            saved_files.append(file_path)
        else:
            flash(f'File {file.filename} is not a valid PDF.')
            return redirect(request.url)
    
    try:
        # Process the uploaded PDFs
        df, issues_df = process_pdfs(saved_files)
        
        # Save processed files
        df_path = os.path.join(PROCESSED_FOLDER, 'IA_inv.xlsx')
        issues_df_path = os.path.join(PROCESSED_FOLDER, 'IA_inv_issues.xlsx')
        df.to_excel(df_path, index=False)
        issues_df.to_excel(issues_df_path, index=False)
        
        # Clean up uploaded files after processing
        for file_path in saved_files:
            os.remove(file_path)
        
        # Pass data to results template
        return render_template('results.html', tables=[df.to_html(classes='table table-striped table-hover', index=False, justify='center'), 
                                                     issues_df.to_html(classes='table table-striped table-hover', index=False, justify='center')],
                               titles=['Invoice Details', 'Identified Issues'],
                               download_links={
                                   'Invoice Details': url_for('download_file', filename='IA_inv.xlsx'),
                                   'Identified Issues': url_for('download_file', filename='IA_inv_issues.xlsx')
                               })
    except Exception as e:
        flash(f'An error occurred during processing: {str(e)}')
        return redirect(url_for('index'))

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(PROCESSED_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
