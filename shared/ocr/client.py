import requests

def extract(pdf_path: str) -> dict:
    # Call Nemotron OCR v1 via NIM API
    url = 'https://integrate.api.nvidia.com/v1'
    headers = {'Authorization': 'Bearer <YOUR_API_KEY>'}
    data = {'model': 'nvidia/nemotron-ocr-v1', 'input': pdf_path}
    response = requests.post(url, headers=headers, json=data)
    return response.json()