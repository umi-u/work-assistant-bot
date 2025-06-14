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

# 載入環境變數
load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# OpenAI 設定
openai.api_key = os.getenv('OPENAI_API_KEY')

class WorkAssistant:
    def __init__(self):
        self.user_sessions = {}
    
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
                5. 處理會議記錄和語音轉文字
                
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
    
    def transcribe_audio(self, audio_content, filename):
        """語音轉文字功能"""
        try:
            # 創建臨時檔案
            with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as temp_file:
                temp_file.write(audio_content)
                temp_file_path = temp_file.name
            
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
            
            # 使用AI分析和摘要
            summary = self.analyze_transcription(transcribed_text)
            
            return transcribed_text, summary
            
        except Exception as e:
            # 清理臨時檔案（如果存在）
            try:
                os.unlink(temp_file_path)
            except:
                pass
            return None, f"語音轉文字處理失敗：{str(e)}"
    
    def analyze_transcription(self, text):
        """分析轉錄文字並生成摘要"""
        try:
            analysis_prompt = f"""請分析以下會議或語音記錄，並提供結構化摘要：

原始內容：
{text}

請提供：
1. 🎯 重點摘要（2-3句話）
2. 📋 主要討論議題
3. ✅ 決議事項（如果有）
4. 📝 行動項目（如果有）
5. 👥 重要人物或提及對象（如果有）

請用繁體中文回應，格式清晰易讀。"""

            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": analysis_prompt}],
                max_tokens=500,
                temperature=0.3
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            return f"摘要分析失敗：{str(e)}"
    
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
• 🎙️ 語音轉文字（NEW！）

💬 使用方式：
• 直接對話：「幫我規劃明天的工作」
• 尋求建議：「如何提高工作效率？」
• 文件協助：「幫我寫會議紀錄」
• 🎙️ 語音記錄：直接發送語音訊息

🎯 快捷指令：
• 「今日規劃」- 獲得當日工作建議
• 「效率技巧」- 查看提升效率的方法
• 「時間管理」- 學習時間管理技巧

🎙️ 語音功能：
• 發送語音訊息自動轉為文字
• 智能摘要和重點提取
• 會議記錄整理

就像跟同事聊天一樣，告訴我你的工作需求吧！"""

        # 今日規劃
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

🎙️ 語音功能提示：
可以直接發送語音訊息，我會自動轉換為文字並整理摘要！

有特定的工作項目需要安排嗎？告訴我詳情，我可以給你更具體的建議！"""

        # 效率技巧
        elif message_lower in ['效率技巧', '提高效率', '工作效率', '效率']:
            return """⚡ 工作效率提升秘訣

🍅 番茄工作法
• 25分鐘專注工作 + 5分鐘休息
• 完成4個番茄後休息15-30分鐘
• 避免在番茄時間內處理干擾

📝 任務優先級管理
• 重要且緊急：立即處理
• 重要不緊急：安排時間處理  
• 緊急不重要：委派或快速處理
• 不重要不緊急：刪除或最後處理

🎯 專注力提升
• 關閉非必要通知
• 準備完整的工作環境
• 一次只專注一件事

📱 工具應用
• 使用待辦清單App
• 設定時間提醒
• 定期檢視和調整計劃

🎙️ 語音記錄技巧：
• 會議時可錄音後發送給我整理
• 語音備忘比打字更快速
• 走路時的靈感可隨時記錄

想深入了解哪個技巧？或有特定的效率問題想討論？"""

        # 時間管理
        elif message_lower in ['時間管理', '管理時間']:
            return """⏰ 時間管理實用技巧

📊 時間分析
• 記錄一週的時間使用
• 找出時間浪費的環節
• 識別最有效率的時段

🎯 目標設定
• 設定SMART目標（具體、可衡量、可達成、相關、有時限）
• 將大目標分解成小任務
• 定期檢視進度

📅 行程規劃
• 前一天晚上規劃隔天行程
• 預留緩衝時間處理突發狀況
• 將相似任務集中處理

🚫 學會說不
• 評估新任務的重要性
• 避免過度承諾
• 專注在最重要的事情上

🎙️ 語音記錄應用：
• 語音日記追蹤時間使用
• 快速記錄會議決議
• 隨手記錄突發想法

需要針對特定情況制定時間管理策略嗎？例如：專案管理、會議安排等？"""

        return None  # 如果不是快捷指令，返回None

# 創建助理實例
assistant = WorkAssistant()

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
    """處理語音訊息 - 新功能！"""
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
        
        print(f"語音檔案大小: {len(audio_content)} bytes")
        
        # 調用語音轉文字
        transcribed_text, summary = assistant.transcribe_audio(audio_content, f"audio_{audio_id}.m4a")
        
        if transcribed_text:
            # 成功轉換，發送結果
            response_text = f"""🎙️ 語音轉文字完成！

📝 原始內容：
{transcribed_text}

{summary}

💡 您可以繼續詢問相關問題，或發送更多語音記錄！"""
            
            print(f"語音轉文字成功: {transcribed_text[:100]}...")
            
        else:
            # 轉換失敗
            response_text = f"""❌ 語音處理失敗

{summary}

請確認：
• 語音檔案大小不超過25MB
• 說話清晰，避免過多背景噪音
• 可以嘗試重新錄音發送"""
            
            print(f"語音轉文字失敗: {summary}")
        
        # 發送處理結果
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=response_text)
        )
        
    except Exception as e:
        error_msg = f"""❌ 語音處理出現錯誤

錯誤詳情：{str(e)}

建議：
• 請稍後再試
• 確認語音檔案格式正確
• 可以嘗試重新錄音"""
        
        print(f"語音處理錯誤: {str(e)}")
        
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
• 🎙️ 發送語音訊息進行轉文字
• 💬 文字描述圖片內容，我可以協助分析

敬請期待更多功能！"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        
    elif isinstance(event.message, FileMessage):
        # 檢查是否為音頻檔案
        file_name = getattr(event.message, 'fileName', '')
        file_type = file_name.lower() if file_name else ''
        
        # 支援的音頻格式
        audio_extensions = ['.mp3', '.m4a', '.wav', '.aac', '.ogg', '.flac', '.opus']
        
        if any(file_type.endswith(ext) for ext in audio_extensions):
            # 處理音頻檔案
            handle_audio_file(event)
        else:
            # 處理其他檔案
            reply_text = """📄 收到您的檔案！

🔧 檔案處理功能正在開發中，即將支援：
• 📊 Excel數據分析
• 📝 Word文檔處理
• 📑 PDF內容解析

💡 目前您可以：
• 🎙️ 發送音頻檔案自動轉文字
• 💬 描述檔案內容，我可以協助分析

📎 檔案名稱：{file_name}

敬請期待更多功能！""".format(file_name=file_name or "未知檔案")
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
    else:
        reply_text = """📎 收到您的檔案！

🎙️ 目前支援語音轉文字功能，其他檔案處理功能正在開發中。

請發送語音訊息或音頻檔案體驗轉文字功能！"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

def handle_audio_file(event):
    """處理音頻檔案上傳"""
    user_id = event.source.user_id
    file_id = event.message.id
    file_name = getattr(event.message, 'fileName', f'audio_{file_id}')
    
    print(f"收到用戶 {user_id} 的音頻檔案: {file_name}")
    
    try:
        # 發送處理中訊息
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"🎙️ 正在處理您的音頻檔案「{file_name}」，請稍候...")
        )
        
        # 下載音頻檔案
        message_content = line_bot_api.get_message_content(file_id)
        audio_content = b""
        for chunk in message_content.iter_content():
            audio_content += chunk
        
        print(f"音頻檔案大小: {len(audio_content)} bytes")
        
        # 調用語音轉文字
        transcribed_text, summary = assistant.transcribe_audio(audio_content, file_name)
        
        if transcribed_text:
            # 成功轉換，發送結果
            response_text = f"""🎙️ 音頻檔案轉文字完成！

📎 檔案名稱：{file_name}
📝 原始內容：
{transcribed_text}

{summary}

💡 您可以繼續詢問相關問題，或發送更多音頻檔案！"""
            
            print(f"音頻檔案轉文字成功: {transcribed_text[:100]}...")
            
        else:
            # 轉換失敗
            response_text = f"""❌ 音頻檔案處理失敗

📎 檔案：{file_name}
{summary}

請確認：
• 音頻檔案大小不超過25MB
• 檔案格式：mp3, m4a, wav, aac等
• 音頻內容清晰，避免過多背景噪音
• 可以嘗試重新上傳檔案"""
            
            print(f"音頻檔案轉文字失敗: {summary}")
        
        # 發送處理結果
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=response_text)
        )
        
    except Exception as e:
        error_msg = f"""❌ 音頻檔案處理出現錯誤

📎 檔案：{file_name}
錯誤詳情：{str(e)}

建議：
• 請稍後再試
• 確認音頻檔案格式正確（mp3, m4a, wav等）
• 檔案大小不超過25MB
• 可以嘗試重新上傳"""
        
        print(f"音頻檔案處理錯誤: {str(e)}")
        
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=error_msg)
        )

@handler.add(PostbackEvent)
def handle_postback(event):
    """處理按鈕點擊等互動事件"""
    data = event.postback.data
    user_id = event.source.user_id
    
    reply_text = f"處理互動操作：{data}"
    
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
    <p>🎙️ 新功能：語音轉文字</p>
    <p>📱 掃描QR Code將Bot加為LINE好友開始使用</p>
    <p>🔧 狀態：準備就緒</p>
    """

@app.route("/test")
def test():
    """測試端點"""
    return {
        "status": "OK",
        "message": "工作助理Bot運行正常",
        "features": ["AI對話", "語音轉文字", "工作規劃建議"],
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 工作助理Bot啟動中...")
    print(f"🎙️ 語音轉文字功能已啟用")
    print(f"📡 監聽端口: {port}")
    print(f"🌐 本地測試: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)