#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試 Direct Query API 端點

使用方法:
    python test_direct_query.py
"""

import requests
import sys
import json

# API 端點
API_URL = "http://localhost:8000/v1/direct_query"

# 測試案例
test_cases = [
    {
        "name": "測試 Google Gemini 模型",
        "data": {
            "model_name": "gemma-3-7b-it",
            "provider": "Google",
            "prompt": "請用一句話介紹什麼是人工智能",
            "temperature": 0.7,
            "max_tokens": 100
        }
    },
    {
        "name": "測試 GitHub Models (如果有配額)",
        "data": {
            "model_name": "openai/gpt-4o-mini",
            "provider": "GitHub",
            "prompt": "Python 是什麼？",
            "temperature": 0.5,
            "max_tokens": 50
        }
    },
    {
        "name": "測試本地 Ollama 模型 (需要運行 Ollama)",
        "data": {
            "model_name": "qwen3:4b-instruct",
            "provider": "Ollama",
            "prompt": "你好",
            "max_tokens": 30
        }
    },
    {
        "name": "測試不存在的模型 (應該會失敗)",
        "data": {
            "model_name": "non-existent-model-12345",
            "provider": "Google",
            "prompt": "測試",
        }
    }
]


def test_direct_query(test_case):
    """測試單個案例"""
    print(f"\n{'='*60}")
    print(f"測試: {test_case['name']}")
    print(f"{'='*60}")
    print(f"請求數據: {json.dumps(test_case['data'], indent=2, ensure_ascii=False)}")
    print()
    
    try:
        response = requests.post(API_URL, json=test_case['data'], timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            answer = result["choices"][0]["message"]["content"]
            model = result.get("model", "unknown")
            usage = result.get("usage", {})
            
            print(f"✅ 成功!")
            print(f"模型: {model}")
            print(f"回答: {answer}")
            print(f"Token 使用: {usage}")
            return True
        else:
            print(f"❌ 失敗 (HTTP {response.status_code})")
            try:
                error_detail = response.json().get('detail', response.text)
                print(f"錯誤: {error_detail}")
            except:
                print(f"錯誤: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"❌ 請求超時")
        return False
    except requests.exceptions.ConnectionError:
        print(f"❌ 無法連接到 API 服務器")
        print(f"   請確認服務器是否在 http://localhost:8000 運行")
        return False
    except Exception as e:
        print(f"❌ 異常: {type(e).__name__}: {e}")
        return False


def main():
    print("=" * 60)
    print("Direct Query API 測試")
    print("=" * 60)
    print(f"API 端點: {API_URL}")
    
    # 先檢查服務器是否運行
    try:
        health_response = requests.get("http://localhost:8000/health", timeout=5)
        if health_response.status_code == 200:
            print("✅ API 服務器運行中")
        else:
            print("⚠️  API 服務器響應異常")
    except:
        print("❌ 無法連接到 API 服務器")
        print("   請先啟動: python api.py")
        sys.exit(1)
    
    # 運行測試
    results = []
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n[{i}/{len(test_cases)}]", end=" ")
        success = test_direct_query(test_case)
        results.append((test_case['name'], success))
    
    # 總結
    print(f"\n{'='*60}")
    print("測試總結")
    print(f"{'='*60}")
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for name, success in results:
        status = "✅ 通過" if success else "❌ 失敗"
        print(f"{status}: {name}")
    
    print(f"\n總計: {passed}/{total} 通過")
    
    if passed == total:
        print("\n🎉 所有測試通過!")
    else:
        print(f"\n⚠️  {total - passed} 個測試失敗")
        print("\n注意: 某些失敗可能是正常的（例如配額用盡、模型不存在等）")


if __name__ == "__main__":
    main()
