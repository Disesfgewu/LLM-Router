#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試配額追蹤功能的腳本
驗證 gemma-3-27b-it 的配額在每次 pre-chat 調用後會正確扣減
"""

import requests
import json
import time

API_URL = "http://localhost:8000/v1/chat/completions"
ADMIN_URL = "http://localhost:8000/admin/status"

def get_quota_status():
    """獲取配額狀態"""
    try:
        response = requests.get(ADMIN_URL)
        response.raise_for_status()
        data = response.json()
        
        # 查找 gemma-3-27b-it 的配額
        for model_info in data.get("models", []):
            if model_info["model_id"] == "gemma-3-27b-it":
                return model_info["remaining"]
        return None
    except Exception as e:
        print(f"獲取配額失敗: {e}")
        return None

def test_chat_with_memory_keyword(message: str):
    """發送包含記憶關鍵字的請求"""
    payload = {
        "model": "auto",
        "messages": [
            {"role": "user", "content": message}
        ],
        "temperature": 0.7
    }
    
    try:
        response = requests.post(API_URL, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"請求失敗: {e}")
        return False

def main():
    print("=" * 60)
    print("測試 gemma-3-27b-it 配額追蹤功能")
    print("=" * 60)
    
    # 1. 檢查初始配額
    print("\n1. 檢查初始配額...")
    initial_quota = get_quota_status()
    if initial_quota is None:
        print("❌ 無法獲取初始配額，請確保 API 服務正在運行")
        return
    print(f"✅ gemma-3-27b-it 初始配額: {initial_quota}")
    
    # 2. 發送包含記憶關鍵字的請求（會觸發 pre-chat）
    print("\n2. 發送包含記憶關鍵字的請求（觸發 pre-chat）...")
    if not test_chat_with_memory_keyword("請幫我查看一下剛剛的記錄"):
        print("❌ 請求失敗")
        return
    print("✅ 請求成功")
    
    # 3. 等待一下讓配額更新
    time.sleep(1)
    
    # 4. 檢查配額是否扣減
    print("\n3. 檢查配額是否扣減...")
    after_quota = get_quota_status()
    if after_quota is None:
        print("❌ 無法獲取更新後的配額")
        return
    print(f"✅ gemma-3-27b-it 當前配額: {after_quota}")
    
    # 5. 驗證配額變化
    print("\n4. 驗證配額變化...")
    if after_quota == initial_quota - 1:
        print(f"✅ 配額正確扣減！({initial_quota} → {after_quota})")
        print("✅ 配額追蹤功能正常工作！")
    elif after_quota == initial_quota:
        print(f"⚠️  配額未變化 ({initial_quota} → {after_quota})")
        print("可能原因：")
        print("  - pre-chat 使用了關鍵字匹配（未調用模型）")
        print("  - 模型調用失敗")
        print("  - 請查看 app/app.log 獲取詳細信息")
    else:
        print(f"⚠️  配額變化異常 ({initial_quota} → {after_quota})")
    
    # 6. 再發送一次請求確認
    print("\n5. 再發送一次請求確認功能穩定性...")
    if test_chat_with_memory_keyword("之前有什麼錯誤嗎？"):
        time.sleep(1)
        final_quota = get_quota_status()
        if final_quota is not None:
            print(f"✅ 第二次請求後配額: {final_quota}")
            if final_quota == after_quota - 1:
                print("✅ 配額持續正確扣減！")
            else:
                print(f"⚠️  配額變化: {after_quota} → {final_quota}")
    
    print("\n" + "=" * 60)
    print("測試完成！請查看 app/app.log 獲取詳細日誌：")
    print("  tail -f app/app.log | grep -E '\\[Pre-chat\\]|\\[Memory\\]'")
    print("=" * 60)

if __name__ == "__main__":
    main()
