#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試記憶功能的腳本
"""

import requests
import json
import time

API_URL = "http://localhost:8000/v1/chat/completions"

def test_chat(message: str, model: str = "auto") -> str:
    """發送聊天請求並返回回應"""
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": message}
        ],
        "temperature": 0.7
    }
    
    print(f"\n{'='*60}")
    print(f"發送問題: {message}")
    print(f"{'='*60}")
    
    try:
        response = requests.post(API_URL, json=payload)
        response.raise_for_status()
        data = response.json()
        
        answer = data["choices"][0]["message"]["content"]
        print(f"回答: {answer}")
        print(f"使用模型: {data.get('model', 'unknown')}")
        
        return answer
    except Exception as e:
        print(f"錯誤: {e}")
        return ""

def main():
    print("開始測試記憶功能...")
    print("\n測試 1: 一般問題（不應該觸發記憶）")
    test_chat("你好，請介紹一下你自己")
    
    time.sleep(2)
    
    print("\n測試 2: 包含記憶關鍵字的問題（應該觸發記憶並查詢 log）")
    test_chat("請幫我查看一下剛剛的記錄")
    
    time.sleep(2)
    
    print("\n測試 3: 另一個記憶相關的問題")
    test_chat("之前有什麼錯誤嗎？請查看 log")
    
    time.sleep(2)
    
    print("\n測試 4: 使用英文關鍵字")
    test_chat("What's in the memory?")
    
    print("\n\n測試完成！")
    print("請檢查 app/app.log 查看詳細的執行日誌")

if __name__ == "__main__":
    main()
