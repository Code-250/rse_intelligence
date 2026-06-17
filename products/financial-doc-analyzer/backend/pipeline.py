from shared.ocr.client import extract
from products.financial-doc-analyzer.backend.prompts.analysis import financial_analysis_system_prompt
import time

def process_document(pdf_path: str) -> dict:
    start_time = time.time()
    ocr_output = extract(pdf_path)
    full_text = ocr_output['full_text']
    if len(ocr_output['pages']) = 20:
        # Send full text to Nemotron 70B for financial analysis
        url = 'https://integrate.api.nvidia.com/v1'
        headers = {'Authorization': 'Bearer <YOUR_API_KEY>'}
        data = {'model': 'nvidia/llama-3.1-nemotron-70b-instruct', 'input': full_text, 'prompt': financial_analysis_system_prompt}
        response = requests.post(url, headers=headers, json=data)
        analysis_output = response.json()
    else:
        # Send full text to DeepSeek V4 Flash for deep analysis
        url = 'https://integrate.api.nvidia.com/v1'
        headers = {'Authorization': 'Bearer <YOUR_API_KEY>'}
        data = {'model': 'deepseek-ai/deepseek-v4-flash', 'input': full_text}
        response = requests.post(url, headers=headers, json=data)
        analysis_output = response.json()
    end_time = time.time()
    processing_ms = (end_time - start_time) * 1000
    return {'summary': analysis_output['summary'], 'key_ratios': analysis_output['key_ratios'], 'risk_flags': analysis_output['risk_flags'], 'verdict': analysis_output['verdict'], 'model_used': analysis_output['model_used'], 'processing_ms': processing_ms}