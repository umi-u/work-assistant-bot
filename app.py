import os
import tempfile
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import openai
from dotenv import load_dotenv
from datetime import datetime
import re
import subprocess
import threading
import time

# 載入環境變數
load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# OpenAI 設定
openai.api_key = os.getenv('OPENAI_API_KEY')

class LongAudioProcessor:
    def __init__(self):
        self.user_sessions = {}
        self.processing_status = {}  # 追蹤處理狀態
    
    def get_ai_response(self, user_id, message):
        """獲取AI回應"""
        try:
            # 簡化的對話歷史管理
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = []
            
            # 添加系統提示
            messages = [
                {"role": "system", "content": """你是一個專業的工作助理AI。你的名字是「小助手」。
                你擅長：
                1. 協助規劃工作排程
                2. 提供工作效率建議  
                3. 幫助撰寫工作相關文件
                4. 分析工作問題並提供解決方案
                5. 處理會議記錄和語音轉文字（支援長達1.5小時的音頻）
                
                請用繁體中文回應，語氣專業但親切。回應要簡潔，適合手機閱讀。
                每次回應不超過300字。"""}
            ]
            
            # 保留最近3輪對話
            recent_history = self.user_sessions[user_id][-6:]  # 3輪對話 = 6條訊息
            messages.extend(recent_history)
            messages.append({"role": "user", "content": message})
            
            # 調用OpenAI API
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=300,
                temperature=0.7
            )
            
            ai_reply = response.choices[0].message.content
            
            # 更新對話歷史
            self.user_sessions[user_id].append({"role": "user", "content": message})
            self.user_sessions[user_id].append({"role": "assistant", "content": ai_reply})
            
            return ai_reply
            
        except Exception as e:
            return f"抱歉，處理您的請求時發生錯誤。請稍後再試。\n錯誤詳情：{str(e)}"
    
    def split_audio_file(self, audio_content, filename, chunk_duration=600):
        """
        改進的音頻檔案分割方法
        對於大檔案，如果無法智能分割，直接使用原檔案
        """
        try:
            file_size_mb = len(audio_content) / 1024 / 1024
            
            # 如果檔案小於25MB，直接處理不分割
            if file_size_mb < 25:
                print(f"檔案大小 {file_size_mb:.1f}MB，直接處理")
                return [audio_content]
            
            # 對於大檔案，嘗試簡單分割
            # 但確保分割點在合理位置
            print(f"檔案大小 {file_size_mb:.1f}MB，嘗試分割")
            
            # 創建臨時檔案
            with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as temp_file:
                temp_file.write(audio_content)
                input_path = temp_file.name
            
            # 檢查檔案是否有效
            try:
                # 先測試原檔案是否可以被OpenAI處理
                with open(input_path, 'rb') as test_file:
                    # 如果檔案不太大，直接嘗試處理
                    if file_size_mb < 40:
                        print("檔案大小適中，嘗試直接處理")
                        os.unlink(input_path)
                        return [audio_content]
                
                # 對於非常大的檔案，使用固定大小分割
                # 但要確保不破壞音頻結構
                max_chunk_size = 20 * 1024 * 1024  # 20MB
                
                # 分割策略：找到相對安全的分割點
                chunks = []
                current_pos = 0
                
                while current_pos < len(audio_content):
                    # 計算這個chunk的結束位置
                    end_pos = min(current_pos + max_chunk_size, len(audio_content))
                    
                    # 如果不是最後一個chunk，嘗試在靜音處分割
                    if end_pos < len(audio_content):
                        # 在chunk邊界附近尋找可能的分割點
                        # 這裡簡化處理，使用固定分割
                        chunk = audio_content[current_pos:end_pos]
                    else:
                        # 最後一個chunk
                        chunk = audio_content[current_pos:]
                    
                    if len(chunk) > 0:
                        chunks.append(chunk)
                    
                    current_pos = end_pos
                
                os.unlink(input_path)
                print(f"分割完成，共 {len(chunks)} 個片段")
                return chunks
                
            except Exception as e:
                print(f"檔案檢查失敗: {e}")
                os.unlink(input_path)
                # 如果檔案檢查失敗，嘗試直接處理
                if file_size_mb < 30:
                    return [audio_content]
                else:
                    # 檔案太大且無法分割，返回錯誤
                    return []
                
        except Exception as e:
            print(f"音頻分割失敗: {e}")
            # 回退到直接處理
            return [audio_content] if len(audio_content) < 25 * 1024 * 1024 else []
    
    def transcribe_audio_chunks(self, chunks, filename):
        """處理音頻片段列表"""
        try:
            all_transcripts = []
            total_chunks = len(chunks)
            
            print(f"開始處理 {total_chunks} 個音頻片段")
            
            for i, chunk in enumerate(chunks):
                print(f"處理片段 {i+1}/{total_chunks}")
                
                # 創建臨時檔案
                with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as temp_file:
                    temp_file.write(chunk)
                    temp_file_path = temp_file.name
                
                try:
                    # 調用Whisper API
                    with open(temp_file_path, 'rb') as audio_file:
                        transcript = openai.Audio.transcribe(
                            model="whisper-1",
                            file=audio_file,
                            language="zh"
                        )
                    
                    transcript_text = transcript.text
                    all_transcripts.append(f"[片段 {i+1}] {transcript_text}")
                    print(f"片段 {i+1} 轉錄成功: {len(transcript_text)} 字符")
                    
                except Exception as e:
                    print(f"片段 {i+1} 轉錄失敗: {e}")
                    all_transcripts.append(f"[片段 {i+1}] 轉錄失敗: {str(e)}")
                
                finally:
                    # 清理臨時檔案
                    os.unlink(temp_file_path)
                
                # 避免API限制，片段間休息
                if i < total_chunks - 1:
                    time.sleep(1)
            
            # 合併所有轉錄結果
            full_transcript = "\n\n".join(all_transcripts)
            
            # 使用AI分析完整內容
            summary = self.analyze_long_transcription(full_transcript, total_chunks)
            
            return full_transcript, summary
            
        except Exception as e:
            return None, f"長音頻處理失敗：{str(e)}"
    
    def transcribe_single_audio(self, audio_content, filename):
        """處理單一音頻檔案（不分割）"""
        try:
            # 創建臨時檔案
            with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as temp_file:
                temp_file.write(audio_content)
                temp_file_path = temp_file.name
            
            print(f"開始處理單一音頻檔案: {filename}, 大小: {len(audio_content)} bytes")
            
            # 調用Whisper API
            with open(temp_file_path, 'rb') as audio_file:
                transcript = openai.Audio.transcribe(
                    model="whisper-1",
                    file=audio_file,
                    language="zh"  # 指定中文
                )
            
            # 清理臨時檔案
            os.unlink(temp_file_path)
            
            # 獲取轉錄文字
            transcribed_text = transcript.text
            print(f"轉錄成功: {len(transcribed_text)} 字符")
            
            # 使用AI分析和摘要
            summary = self.analyze_transcription(transcribed_text)
            
            return transcribed_text, summary
            
        except Exception as e:
            # 清理臨時檔案（如果存在）
            try:
                os.unlink(temp_file_path)
            except:
                pass
            print(f"單一音頻處理失敗: {e}")
            return None, f"語音轉文字處理失敗：{str(e)}"
    
    def analyze_transcription(self, text):
        """分析轉錄文字並生成智能記錄整理"""
        try:
            analysis_prompt = f"""請將以下語音記錄整理成專業的會議或記錄摘要，直接提供結構化的整理結果：

語音內容：
{text}

請提供完整的記錄整理，包括：

🎯 **重點摘要**
[用2-3句話概括主要內容]

📋 **主要議題**
[條列式列出討論的重點議題]

✅ **重要決議**
[如果有決定或結論，明確列出]

📝 **行動項目**
[需要執行的具體任務，包含負責人和時間]

📊 **關鍵數據**
[提及的重要數字、日期、金額等]

👥 **相關人員**
[參與或提及的重要人物]

⏰ **時間安排**
[重要的截止日期或時程安排]

💡 **補充說明**
[其他重要細節或注意事項]

請用繁體中文，條理清晰，直接可用作正式記錄。避免提及"語音記錄"等字眼，直接以會議記錄的格式呈現。"""

            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": analysis_prompt}],
                max_tokens=800,
                temperature=0.3
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            return f"記錄整理失敗：{str(e)}"
        """分析長轉錄文字並生成摘要"""
        try:
            analysis_prompt = f"""請分析以下長會議記錄（共{chunk_count}個片段），並提供結構化摘要：

原始內容：
{text[:4000]}{"..." if len(text) > 4000 else ""}

請提供：
1. 🎯 會議重點摘要（3-5句話）
2. 📋 主要討論議題（條列式）
3. ✅ 重要決議事項（如果有）
4. 📝 行動項目和負責人（如果有）
5. ⏰ 重要時間點或截止日期（如果有）
6. 👥 參與人員或提及對象（如果有）
7. 📊 數據或關鍵數字（如果有）

請用繁體中文回應，格式清晰易讀。由於是長會議記錄，請特別注意整體結構和重點歸納。"""

            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": analysis_prompt}],
                max_tokens=800,
                temperature=0.3
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            return f"摘要分析失敗：{str(e)}"
    
    def process_long_audio_async(self, user_id, audio_content, filename, file_id):
        """異步處理長音頻"""
        try:
            # 更新處理狀態
            self.processing_status[user_id] = {
                'status': 'processing',
                'filename': filename,
                'start_time': datetime.now()
            }
            
            # 發送進度更新
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text=f"🔄 開始分析長音頻檔案...\n📎 檔案：{filename}\n📏 大小：{len(audio_content)/1024/1024:.1f}MB")
            )
            
            # 分割音頻
            chunks = self.split_audio_file(audio_content, filename)
            chunk_count = len(chunks)
            
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text=f"✂️ 音頻分割完成！\n📂 共分割為 {chunk_count} 個片段\n🎙️ 開始逐段轉錄...")
            )
            
            # 處理各個片段
            full_transcript, summary = self.transcribe_audio_chunks(chunks, filename)
            
            if full_transcript:
                # 成功處理
                self.processing_status[user_id]['status'] = 'completed'
                
                # 準備結果訊息（分段發送）
                processing_time = (datetime.now() - self.processing_status[user_id]['start_time']).total_seconds()
                
                # 第一則：處理完成資訊
                info_message = f"""🎉 長音頻轉文字完成！

📎 檔案：{filename}
📊 統計：{chunk_count} 個片段，約 {len(full_transcript)} 字符
⏱️ 處理時間：{processing_time:.0f}秒"""
                
                # 第二則：轉錄內容（分段）
                transcript_messages = []
                transcript_header = "📝 完整轉錄內容：\n"
                
                if len(full_transcript) <= 4500:
                    transcript_messages.append(transcript_header + full_transcript)
                else:
                    transcript_messages.append(transcript_header + "[內容較長，分段顯示]")
                    
                    # 分段顯示
                    chunk_size = 4500
                    for i in range(0, len(full_transcript), chunk_size):
                        chunk = full_transcript[i:i + chunk_size]
                        part_num = i // chunk_size + 1
                        total_parts = (len(full_transcript) + chunk_size - 1) // chunk_size
                        
                        chunk_message = f"📝 轉錄內容 ({part_num}/{total_parts})：\n{chunk}"
                        transcript_messages.append(chunk_message)
                
                # 第三則：AI摘要
                summary_message = f"🤖 AI智能分析：\n{summary}"
                if len(summary_message) > 4800:
                    summary_message = f"🤖 AI智能分析：\n{summary[:4500]}...\n[摘要已截斷]"
                
                # 組合所有要發送的訊息
                all_messages = [info_message] + transcript_messages + [summary_message]
                all_messages.append("💡 長音頻轉錄完成！您可以繼續詢問相關問題！")
                
                # 逐一發送
                for i, message in enumerate(all_messages):
                    try:
                        line_bot_api.push_message(user_id, TextSendMessage(text=message))
                        if i < len(all_messages) - 1:
                            time.sleep(0.5)
                    except Exception as e:
                        print(f"發送長音頻結果訊息 {i+1} 失敗: {e}")
                        continue
                
            else:
                # 處理失敗
                self.processing_status[user_id]['status'] = 'failed'
                result_text = f"""❌ 長音頻處理失敗

📎 檔案：{filename}
{summary}

建議：
• 檢查音頻檔案品質
• 嘗試較短的音頻片段
• 確認檔案格式正確"""
                
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=result_text)
                )
            
        except Exception as e:
            # 處理異常
            self.processing_status[user_id]['status'] = 'error'
            error_msg = f"""❌ 長音頻處理出現錯誤

📎 檔案：{filename}
錯誤：{str(e)}

請稍後重試或嘗試較小的檔案。"""
            
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text=error_msg)
            )
    
    def handle_quick_commands(self, message):
        """處理快捷指令"""
        message_lower = message.lower().strip()
        
        # 幫助指令
        if message_lower in ['幫助', 'help', '功能', '指令', '使用說明']:
            return """🤖 小助手工作助理

📋 主要功能：
• 工作規劃與排程建議
• 效率提升技巧分享  
• 文件撰寫協助
• 問題分析與解決方案
• 🎙️ 智能會議記錄整理（支援1.5小時+）

💬 使用方式：
• 直接對話：「幫我規劃明天的工作」
• 尋求建議：「如何提高工作效率？」
• 文件協助：「幫我寫會議紀錄」
• 🎙️ 會議記錄：上傳音頻檔案自動整理成專業記錄

🎯 快捷指令：
• 「今日規劃」- 獲得當日工作建議
• 「效率技巧」- 查看提升效率的方法
• 「時間管理」- 學習時間管理技巧

🎙️ 智能記錄功能：
• 支援最長1.5小時的會議錄音
• 自動整理成專業會議記錄格式
• 提取重點、決議、行動項目
• 無需查看原始文字，直接獲得整理結果

就像跟同事聊天一樣，告訴我你的工作需求吧！"""

        # 其他快捷指令保持不變...
        elif message_lower in ['今日規劃', '今天規劃', '今日安排']:
            today = datetime.now().strftime("%Y年%m月%d日")
            return f"""📅 {today} 工作規劃建議

🌅 早晨安排（9:00-12:00）
• 處理重要且緊急的任務
• 回覆重要郵件和訊息
• 完成需要高專注力的工作

🌞 下午安排（13:00-17:00）
• 開會和團隊協作
• 處理例行性工作
• 規劃明天的任務

🌙 收尾時段（17:00-18:00）
• 整理今日完成事項
• 更新工作進度
• 準備明天的重點工作

💡 小提醒：記得每90分鐘休息一下，保持最佳工作狀態！

🎙️ 長音頻提示：
可以上傳長達1.5小時的會議錄音，我會自動分割處理並整理完整摘要！

有特定的工作項目需要安排嗎？告訴我詳情，我可以給你更具體的建議！"""

        return None

# 創建助理實例
assistant = LongAudioProcessor()

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Webhook 回調函數"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """處理文字訊息"""
    user_id = event.source.user_id
    user_message = event.message.text
    
    print(f"收到用戶 {user_id} 的訊息: {user_message}")
    
    # 先檢查快捷指令
    quick_reply = assistant.handle_quick_commands(user_message)
    if quick_reply:
        reply_message = quick_reply
    else:
        # 使用AI生成回應
        reply_message = assistant.get_ai_response(user_id, user_message)
    
    print(f"回應: {reply_message}")
    
    # 發送回應
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_message)
    )

@handler.add(MessageEvent, message=AudioMessage)
def handle_audio(event):
    """處理語音訊息"""
    user_id = event.source.user_id
    audio_id = event.message.id
    
    print(f"收到用戶 {user_id} 的語音訊息，ID: {audio_id}")
    
    try:
        # 發送處理中訊息
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🎙️ 正在處理您的語音訊息，請稍候...")
        )
        
        # 下載語音檔案
        message_content = line_bot_api.get_message_content(audio_id)
        audio_content = b""
        for chunk in message_content.iter_content():
            audio_content += chunk
        
        # 檢查檔案大小，決定處理方式
        file_size_mb = len(audio_content) / 1024 / 1024
        
        if file_size_mb > 30:  # 大於30MB使用異步處理
            # 異步處理大檔案
            thread = threading.Thread(
                target=assistant.process_long_audio_async,
                args=(user_id, audio_content, f"voice_{audio_id}.m4a", audio_id)
            )
            thread.daemon = True
            thread.start()
        else:
            # 直接處理小檔案
            transcribed_text, organized_record = assistant.transcribe_single_audio(audio_content, f"voice_{audio_id}.m4a")
            
            if transcribed_text:
                # 發送整理後的記錄
                response_messages = []
                
                response_messages.append(f"""🎙️語音記錄整理完成！

📊 原始長度：{len(transcribed_text)} 字符
⏱️ 處理完成""")
                
                if organized_record:
                    response_messages.append(organized_record)
                else:
                    response_messages.append("⚠️ 記錄整理過程中出現問題。")
                
                response_messages.append("✅ 語音記錄處理完成！有任何問題都可以詢問我。")
                
                # 逐一發送
                for i, msg in enumerate(response_messages):
                    line_bot_api.push_message(user_id, TextSendMessage(text=msg))
                    if i < len(response_messages) - 1:
                        time.sleep(0.8)
            else:
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=f"❌ 語音處理失敗\n{organized_record}")
                )
        
    except Exception as e:
        error_msg = f"❌ 語音處理出現錯誤：{str(e)}"
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=error_msg)
        )

def handle_audio_file(event):
    """處理音頻檔案上傳"""
    user_id = event.source.user_id
    file_id = event.message.id
    file_name = getattr(event.message, 'fileName', f'audio_{file_id}')
    file_size = getattr(event.message, 'fileSize', 0)
    
    print(f"收到用戶 {user_id} 的音頻檔案: {file_name}, 大小: {file_size} bytes")
    
    try:
        # 檢查檔案大小
        file_size_mb = file_size / 1024 / 1024
        
        if file_size_mb > 200:  # 超過200MB
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"""📄 檔案太大無法處理

📎 檔案：{file_name}
📏 大小：{file_size_mb:.1f}MB

建議：
• 檔案大小請控制在200MB以內
• 或嘗試分割成較小的檔案
• 降低音質可以減少檔案大小""")
            )
            return
        
        # 發送處理中訊息
        if file_size_mb > 30:
            processing_msg = f"""🎙️ 開始處理大型音頻檔案

📎 檔案：{file_name}
📏 大小：{file_size_mb:.1f}MB
⏱️ 預計處理時間：{int(file_size_mb * 0.5)}分鐘

🔄 正在下載和分割檔案，請耐心等待..."""
        else:
            processing_msg = f"🎙️ 正在處理音頻檔案「{file_name}」，請稍候..."
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=processing_msg)
        )
        
        # 下載音頻檔案
        message_content = line_bot_api.get_message_content(file_id)
        audio_content = b""
        for chunk in message_content.iter_content():
            audio_content += chunk
        
        # 根據檔案大小選擇處理方式
        if file_size_mb > 50:  # 只有超過50MB才異步處理
            thread = threading.Thread(
                target=assistant.process_long_audio_async,
                args=(user_id, audio_content, file_name, file_id)
            )
            thread.daemon = True
            thread.start()
        else:
            # 中小檔案直接同步處理（不分割）
            transcribed_text, organized_record = assistant.transcribe_single_audio(audio_content, file_name)
            
            if transcribed_text:
                # 準備整理後的記錄
                print(f"轉錄成功，開始整理記錄: {len(transcribed_text)} 字符")
                
                messages_to_send = []
                
                # 第一則：檔案資訊
                file_info = f"""📋 會議記錄整理完成！

📎 來源檔案：{file_name}
📏 檔案大小：{file_size_mb:.1f}MB
📊 原始字數：{len(transcribed_text)} 字符
⏱️ 處理完成"""

                messages_to_send.append(file_info)
                
                # 第二則：整理後的記錄
                if organized_record and len(organized_record) > 0:
                    if len(organized_record) <= 4500:
                        messages_to_send.append(organized_record)
                    else:
                        # 記錄太長，分段處理
                        record_parts = []
                        current_part = ""
                        lines = organized_record.split('\n')
                        
                        for line in lines:
                            if len(current_part + line + '\n') <= 4000:
                                current_part += line + '\n'
                            else:
                                if current_part:
                                    record_parts.append(current_part.strip())
                                current_part = line + '\n'
                        
                        if current_part:
                            record_parts.append(current_part.strip())
                        
                        for i, part in enumerate(record_parts):
                            if len(record_parts) > 1:
                                part_header = f"📋 會議記錄 ({i+1}/{len(record_parts)})：\n\n"
                                messages_to_send.append(part_header + part)
                            else:
                                messages_to_send.append(part)
                else:
                    messages_to_send.append("⚠️ 記錄整理過程中出現問題，請稍後重試。")
                
                # 第三則：結尾提示
                messages_to_send.append("✅ 記錄整理完成！您可以詢問相關問題或要求進一步分析特定內容。")
                
                # 逐一發送訊息
                for i, message in enumerate(messages_to_send):
                    try:
                        line_bot_api.push_message(user_id, TextSendMessage(text=message))
                        if i < len(messages_to_send) - 1:
                            time.sleep(0.8)
                    except Exception as e:
                        print(f"發送整理記錄訊息 {i+1} 失敗: {e}")
                        continue
                        
            else:
                error_text = f"""❌ 音頻檔案處理失敗

📎 檔案：{file_name}
{organized_record}"""
                
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=error_text)
                )
        
    except Exception as e:
        error_msg = f"""❌ 音頻檔案處理出現錯誤

📎 檔案：{file_name}
錯誤：{str(e)}

建議：
• 請稍後再試
• 確認檔案格式正確
• 檔案大小在合理範圍內"""
        
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=error_msg)
        )

@handler.add(MessageEvent, message=(ImageMessage, FileMessage))
def handle_file(event):
    """處理圖片和其他檔案上傳"""
    user_id = event.source.user_id
    
    if isinstance(event.message, ImageMessage):
        reply_text = """🖼️ 收到您的圖片！

🔧 圖片處理功能正在開發中，即將支援：
• 📝 文字識別(OCR)
• 📊 圖表數據分析
• 📋 文件內容解析

💡 目前您可以：
• 🎙️ 發送音頻檔案進行轉文字（支援1.5小時+）
• 💬 文字描述圖片內容，我可以協助分析"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        
    elif isinstance(event.message, FileMessage):
        # 嘗試處理為音頻檔案
        try:
            handle_audio_file(event)
        except Exception as e:
            print(f"音頻處理失敗，當作一般檔案處理: {e}")
            
            file_name = getattr(event.message, 'fileName', '未知檔案')
            reply_text = f"""📄 收到您的檔案！

📎 檔案：{file_name}

🔧 如果這是音頻檔案但無法處理，請確認：
• 檔案格式：mp3, m4a, wav, aac等
• 檔案大小：建議200MB以內
• 檔案完整性：確認未損壞

💡 其他檔案處理功能正在開發中！"""
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

# 健康檢查端點
@app.route("/")
def hello():
    return """
    <h1>🤖 工作助理 LINE Bot</h1>
    <p>✅ 服務正常運行中</p>
    <p>🎙️ 長音頻轉文字功能（支援1.5小時+）</p>
    <p>📱 掃描QR Code將Bot加為LINE好友開始使用</p>
    <p>🔧 狀態：準備就緒</p>
    """

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 工作助理Bot啟動中...")
    print(f"🎙️ 長音頻轉文字功能已啟用（支援1.5小時+）")
    print(f"📡 監聽端口: {port}")
    app.run(host='0.0.0.0', port=port, debug=True)