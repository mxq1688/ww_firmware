import pdfplumber

pdf_path = r'E:\work\ww_firmware\4g_serial_port\Quectel_LTE_Standard(A)系列_DFOTA_升级指导_V1.4.pdf'
output_path = r'E:\work\ww_firmware\4g_serial_port\DFOTA_Guide.txt'

with pdfplumber.open(pdf_path) as pdf:
    text = ''
    for i, page in enumerate(pdf.pages):
        print(f'Processing page {i+1}/{len(pdf.pages)}...')
        text += page.extract_text() or ''
        text += '\n\n--- PAGE BREAK ---\n\n'
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(text)

print(f'Done! Extracted {len(pdf.pages)} pages to {output_path}')

