#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試文件上傳和內容生成 API

測試 /v1/file/generate_content 端點
"""

import requests
import sys
import os


def test_file_upload(image_path: str, prompt: str = "請描述這張圖片內容，並把圖片中的文字完整擷取出來。"):
    """
    測試文件上傳 API
    
    Args:
        image_path: 圖片文件路徑
        prompt: 提示詞
    """
    api_url = "http://localhost:8000/v1/file/generate_content"
    
    # 檢查文件是否存在
    if not os.path.exists(image_path):
        print(f"❌ 文件不存在: {image_path}")
        return
    
    print(f"📤 上傳文件: {image_path}")
    print(f"💬 提示詞: {prompt}")
    print("=" * 60)
    
    try:
        # 準備文件和表單數據
        with open(image_path, 'rb') as f:
            files = {
                'file': (os.path.basename(image_path), f, 'image/jpeg')
            }
            data = {
                'prompt': prompt,
                'temperature': 0.7
            }
            
            # 發送請求
            print("⏳ 發送請求到 API...")
            response = requests.post(api_url, files=files, data=data, timeout=60)
        
        # 檢查響應
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 請求成功！")
            print("=" * 60)
            print(f"模型: {result['model']}")
            print(f"生成內容:\n{result['choices'][0]['message']['content']}")
            print("=" * 60)
            print(f"Token 使用: {result['usage']}")
        else:
            print(f"\n❌ 請求失敗！狀態碼: {response.status_code}")
            print(f"錯誤信息: {response.text}")
    
    except requests.exceptions.Timeout:
        print("❌ 請求超時，請檢查 API 服務是否正在運行")
    except requests.exceptions.ConnectionError:
        print("❌ 無法連接到 API 服務，請確保服務正在運行 (python api.py)")
    except Exception as e:
        print(f"❌ 發生錯誤: {type(e).__name__}: {e}")


def main():
    print("=" * 60)
    print("測試文件上傳和內容生成 API")
    print("=" * 60)
    
    # 檢查命令行參數
    if len(sys.argv) < 2:
        print("\n使用方法:")
        print(f"  python {sys.argv[0]} <image_path> [prompt]")
        print("\n範例:")
        print(f"  python {sys.argv[0]} test_image.jpg")
        print(f"  python {sys.argv[0]} test_image.jpg \"請描述這張圖片\"")
        return
    
    image_path = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else "請描述這張圖片內容，並把圖片中的文字完整擷取出來。"
    
    test_file_upload(image_path, prompt)


if __name__ == "__main__":
    main()
