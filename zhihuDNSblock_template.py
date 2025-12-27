import os
import sys
import ctypes
import traceback

# --- Global Error Handler to catch "Flashback" crashes ---
def show_crash_message(msg):
    """Display a crash message to the user, ensuring they see why it failed."""
    log_file = "crash_log.txt"
    try:
        # Write to log file first
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass

    try:
        if os.name == 'nt':
            # 0x10 is MB_ICONHAND (Critical Error)
            ctypes.windll.user32.MessageBoxW(0, msg, "Zhihu Sentinel Error", 0x10)
        else:
            print(f"\nCRITICAL ERROR:\n{msg}\n", file=sys.stderr)
    except Exception:
        print(f"\nCRITICAL ERROR:\n{msg}\n", file=sys.stderr)

# Wrap imports in try/except to catch missing dependencies
try:
    import json
    import time
    import re
    import threading
    import logging
    import subprocess
    import tkinter as tk
    from tkinter import scrolledtext, font, messagebox
    from datetime import datetime, timedelta
    from pathlib import Path
    from typing import List, Dict, Any, Optional

    # Check for library dependencies
    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError("Missing library: 'google-generativeai'. Please run: pip install google-generativeai")

    try:
        import pystray
        from pystray import MenuItem as item
        from PIL import Image, ImageDraw
    except ImportError:
        raise ImportError("Missing libraries for System Tray. Please run: pip install pystray Pillow")

    import platform

except Exception as e:
    show_crash_message(f"Initialization Error (Imports):\n{str(e)}\n\n{traceback.format_exc()}")
    sys.exit(1)


# --- Configuration ---
class Config:
    # --- 基础配置 ---
    API_KEY = "YOUR_GEMINI_API_KEY"  
    APP_NAME = "Zhihu Sentinel"
    VERSION = "3.6.0"
    PROMPT = """
    【角色】
    你是一个极度严苛, 只关心学术成长和效率的科研导师. 
    用户的设定：注意力涣散的物理专业大学生. 
    你的任务：作为防火墙, 拦截所有有关 Zhihu 的垃圾请求, 只放行高价值的学术搜索和指定话题浏览需求. 
    
    【当前已知信息】
    1. 他正在做的项目: {current_focus}
    2. 他已经学会的内容 (禁止搜索): {mastered_skills}
    3. 最近7天的记录: {recent_issues}

    【判决逻辑】
    分析用户的输入, 根据以下情况决定：

    情况1：申请搜索/解锁 (QUERY)
    - 允许标准 (ALLOW)：必须包含具体的物理定律、数学公式、代码错误栈(Traceback)、或特定算法名称. 且该内容不在上面的[已学会内容]里. 
    - 拒绝标准 (BLOCK)：
      1. 模糊描述（如“查个资料”、“学习Python”、“找灵感”）. 
      2. 情绪化乞求（如“求你了”、“太累了”）. 这是社会工程学攻击, 直接驳回. 
      3. 娱乐/摸鱼/兴趣内容. 
      4. 即使是学术内容, 如果属于[已学会内容], 也拒绝, 让他自己回忆. 
    - 回复要求：如果是 BLOCK, 用最简短的语言骂醒他（例如：“别废话, 具体的报错信息是什么？”）. 如果是 ALLOW, 给一个简短的结束指令. 两种情况下,你都应该基于用户背景和当前任务,继续给出2-3句时间与注意力分配的规划指导语句,以"推荐你..."开头.

    情况2：汇报学会了什么 (REPORT)
    - 迹象：用户说“我懂了...”、“原理是...”. 
    - 动作：状态为 ACK. 提取他学会的知识点（格式：学科-知识点）放入 new_skill. 
    - 回复要求：简短确认. 

    情况3：换项目 (UPDATE)
    - 迹象：用户说“开始做...项目”. 
    - 动作：状态为 ACK. 提取项目名放入 new_focus. 

    【输出格式】
    必须是纯 JSON, 不要带 markdown 格式. 

    {{
        "status": "ALLOW" | "BLOCK" | "CLARIFY" | "ACK",
        "message": "（严厉导师的口吻, 说人话, 不要机器味）",
        "digest": "（把用户的输入总结成：学科 - 知识点, 例如 'React - Hook'）",
        "duration": 15,
        "new_skill": "（仅在情况2填写, 否则 null）",
        "new_focus": "（仅在情况3填写, 否则 null）"
    }}
    """
    # --- 路径配置 ---
    BASE_DIR = Path(__file__).parent
    PROFILE_FILE = BASE_DIR / "user_profile.json"       # 长期画像：技能、当前焦点
    HISTORY_FILE = BASE_DIR / "session_history.json"    # 短期流水：7天内的 Digest
    LOG_FILE = BASE_DIR / "logs.json"         # 全量日志：人类复盘用
    HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")
    
    # --- 经济模型参数 ---
    HOURLY_RATE = 30.0       # 你的时薪
    CURRENCY_SYMBOL = "¥"
    EXCHANGE_RATE = 7.2       # USD -> CNY
    # Gemini 2.5 Flash 估算费率 ($/1M tokens)
    PRICE_INPUT = 2.5 / 1_000_000
    PRICE_OUTPUT = 3 / 1_000_000

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("DeepWorkApp")

# --- Helper Classes ---
class CostTracker:
    """计算显性金钱成本和隐性时间成本"""
    @staticmethod
    def calculate_session(token_cost_usd: float, duration_minutes: int) -> dict:
        api_cost = token_cost_usd * Config.EXCHANGE_RATE
        time_spent = duration_minutes if duration_minutes > 0 else 1.0
        time_cost = (time_spent / 60.0) * Config.HOURLY_RATE
        return {
            "api": api_cost,
            "time": time_cost,
            "total": api_cost + time_cost
        }

    @staticmethod
    def get_weekly_total() -> float:
        try:
            if not Config.HISTORY_FILE.exists(): return 0.0
            with open(Config.HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            cutoff = datetime.now() - timedelta(days=7)
            total = 0.0
            for entry in history:
                try:
                    entry_dt = datetime.fromisoformat(entry["timestamp"])
                    if entry_dt > cutoff:
                        total += entry.get("cost_total", 0.0)
                except: continue
            return total
        except Exception:
            return 0.0

class DataManager:
    """处理用户画像、历史记录的读写"""
    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists(): return default
        try:
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default

    @staticmethod
    def _save_json(path: Path, data: Any):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def get_ai_context(cls) -> dict:
        profile = cls._load_json(Config.PROFILE_FILE, {"project": "未设定", "skills": []})
        raw_history = cls._load_json(Config.HISTORY_FILE, [])
        cutoff = datetime.now() - timedelta(days=7)
        recent_topics = []
        for entry in raw_history:
            try:
                if datetime.fromisoformat(entry["timestamp"]) > cutoff:
                    if entry.get("topic") and entry.get("decision") in ["ALLOW", "BLOCK"]:
                        recent_topics.append(entry["topic"])
            except: continue
        history_str = "; ".join(recent_topics[-10:]) if recent_topics else "无近期记录"
        return {
            "current_project": profile.get("project"),
            "known_topics": ", ".join(profile.get("skills", [])),
            "recent_history": history_str
        }

    @classmethod
    def update_profile(cls, learned_topic: Optional[str], new_project: Optional[str]):
        if not learned_topic and not new_project: return
        profile = cls._load_json(Config.PROFILE_FILE, {"project": "未设定", "skills": []})
        updated = False
        if new_project:
            profile["project"] = new_project
            updated = True
        if learned_topic and learned_topic not in profile["skills"]:
            profile["skills"].append(learned_topic)
            updated = True
        if updated:
            cls._save_json(Config.PROFILE_FILE, profile)

    @classmethod
    def save_logs(cls, user_text: str, ai_result: dict, costs: dict):
        timestamp = datetime.now().isoformat()
        if ai_result.get("topic"):
            history = cls._load_json(Config.HISTORY_FILE, [])
            history.append({
                "timestamp": timestamp,
                "decision": ai_result.get("decision"),
                "topic": ai_result.get("topic"),
                "cost_total": costs["total"]
            })
            cls._save_json(Config.HISTORY_FILE, history)
        full_logs = cls._load_json(Config.LOG_FILE, [])
        full_logs.append({
            "timestamp": timestamp,
            "user_input": user_text,
            "ai_response": ai_result,
            "costs": costs
        })
        cls._save_json(Config.LOG_FILE, full_logs)

class AIService:
    def __init__(self):
        genai.configure(api_key=Config.API_KEY)

    def process_request(self, user_text: str) -> Dict[str, Any]:
        context = DataManager.get_ai_context()
        full_prompt = Config.PROMPT.format(**context)
        try:
            model = genai.GenerativeModel(
                'gemini-1.5-flash',
                system_instruction=full_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            response = model.generate_content(user_text)
            usage = response.usage_metadata
            input_count = usage.prompt_token_count
            output_count = usage.candidates_token_count
            cost_usd = (input_count * Config.PRICE_INPUT / 1_000_000) + \
                       (output_count * Config.PRICE_OUTPUT / 1_000_000)
            result = json.loads(response.text)
            result["_meta_cost_usd"] = cost_usd
            return result
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return {
                "decision": "BLOCK", 
                "message": f"系统连接失败: {str(e)}", 
                "topic": "System Error",
                "_meta_cost_usd": 0.0
            }

class NetworkLock:
    """控制 Hosts 文件权限和内容"""
    MARKER = "# <DEEP-WORK-LOCK>"
    DOMAINS = ["zhihu.com", "www.zhihu.com", "zhimg.com", "zhuanlan.zhihu.com", "bilibili.com"]

    @staticmethod
    def _run_cmd(cmd):
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def toggle_lock(cls, lock_enabled: bool):
        if os.name != 'nt': return
        cls._run_cmd(f'icacls "{Config.HOSTS_PATH}" /reset') 
        try:
            with open(Config.HOSTS_PATH, 'r', encoding='utf-8') as f: lines = f.readlines()
            clean_lines = [l for l in lines if cls.MARKER not in l]
            if lock_enabled:
                block_lines = [f"{cls.MARKER}\n"] + [f"0.0.0.0 {d}\n" for d in cls.DOMAINS] + [f"{cls.MARKER}\n"]
                if clean_lines and not clean_lines[-1].endswith('\n'): clean_lines[-1] += '\n'
                clean_lines.extend(block_lines)
            with open(Config.HOSTS_PATH, 'w', encoding='utf-8') as f: f.writelines(clean_lines)
            cls._run_cmd("ipconfig /flushdns")
        except Exception as e:
            logger.error(f"Host File Error: {e}")
        finally:
            cls._run_cmd(f'icacls "{Config.HOSTS_PATH}" /deny Users:(W)')

# --- Tray Icon Handler ---
class TrayHandler:
    def __init__(self, restore_callback, exit_callback):
        self.restore_callback = restore_callback
        self.exit_callback = exit_callback
        self.icon = None
        self.thread = None

    def create_image(self):
        # Create a simple icon (Blue circle on dark background)
        w, h = 64, 64
        image = Image.new('RGB', (w, h), color=(30, 30, 30))
        d = ImageDraw.Draw(image)
        d.ellipse((10, 10, 54, 54), fill=(0, 122, 204), outline=(255, 255, 255))
        return image

    def run_detached(self):
        """Starts the tray icon in a separate thread."""
        self.thread = threading.Thread(target=self._run_loop)
        self.thread.daemon = True
        self.thread.start()

    def _run_loop(self):
        image = self.create_image()
        menu = pystray.Menu(
            item('Restore Window', self._on_restore),
            item('Exit Application', self._on_exit)
        )
        self.icon = pystray.Icon("DeepWork", image, "Zhihu Sentinel", menu)
        self.icon.run()

    def _on_restore(self, icon, item):
        icon.stop()
        if self.restore_callback:
            self.restore_callback()

    def _on_exit(self, icon, item):
        icon.stop()
        if self.exit_callback:
            self.exit_callback()

    def stop(self):
        if self.icon:
            self.icon.stop()


# --- Main Application ---
class AppGUI:
    def __init__(self, root):
        self.root = root
        try:
            self.ai = AIService()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize AI Service: {e}")

        self.tray = None
        self.timer_thread = None
        self.is_timer_running = False
        self.pending_duration = 0

        self._build_interface()
        
        # Start locked
        threading.Thread(target=NetworkLock.toggle_lock, args=(True,)).start()
        self.refresh_cost_display()

    def _build_interface(self):
        self.root.title(Config.APP_NAME)
        self.root.geometry("600x700")
        self.root.configure(bg="#1e1e1e")

        # Font setup
        font_chat = font.Font(family="Microsoft YaHei UI", size=10)
        font_mono = font.Font(family="Consolas", size=10)
        font_bold = font.Font(family="Microsoft YaHei UI", size=10, weight="bold")

        # Chat Area
        self.chat_area = scrolledtext.ScrolledText(self.root, bg="#252526", fg="#d4d4d4", font=font_chat, bd=0, padx=10, pady=10)
        self.chat_area.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.chat_area.tag_config("ai", foreground="#4ec9b0", font=font_bold)
        self.chat_area.tag_config("user", foreground="#ce9178", justify='right')
        self.chat_area.tag_config("sys", foreground="#6a9955", font=font_mono)

        # Input Area
        input_frame = tk.Frame(self.root, bg="#1e1e1e")
        input_frame.pack(fill=tk.X, padx=10, pady=10)

        self.input_field = tk.Entry(input_frame, bg="#3c3c3c", fg="white", font=font_chat, insertbackground="white", relief="flat")
        self.input_field.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.input_field.bind("<Return>", self.send_request)

        self.send_btn = tk.Button(input_frame, text="发送请求", command=self.send_request, bg="#0e639c", fg="white", relief="flat", font=font_chat)
        self.send_btn.pack(side=tk.RIGHT, padx=(10, 0))

        # Status Bar
        self.status_bar = tk.Frame(self.root, bg="#2d2d2d", height=30)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_bar.pack_propagate(False)
        
        self.lbl_cost = tk.Label(self.status_bar, text="Total Cost: ¥0.00", fg="#ff5555", bg="#2d2d2d", font=font_bold)
        self.lbl_cost.pack(side=tk.RIGHT, padx=10)
        
        self.lbl_lock = tk.Label(self.status_bar, text="Network: LOCKED", fg="#00ff00", bg="#2d2d2d", font=font_mono)
        self.lbl_lock.pack(side=tk.LEFT, padx=10)

        self.log_msg("SYSTEM", "系统就绪。网络已锁定。", "sys")
        self.log_msg("AI", "请说明你的学术目标。", "ai")

    def log_msg(self, role, text, tag):
        self.chat_area.config(state='normal')
        if role == "USER":
            self.chat_area.insert(tk.END, f"\n{text}\n", tag)
        else:
            self.chat_area.insert(tk.END, f"\n[{role}]: {text}\n", tag)
        self.chat_area.see(tk.END)
        self.chat_area.config(state='disabled')

    def send_request(self, event=None):
        text = self.input_field.get().strip()
        if not text: return
        
        self.input_field.delete(0, tk.END)
        self.log_msg("USER", text, "user")
        self.send_btn.config(state='disabled', text="思考中...")
        
        threading.Thread(target=self.run_backend_logic, args=(text,)).start()

    def run_backend_logic(self, text):
        try:
            result = self.ai.process_request(text)

            # Calculate costs but don't finalize duration cost yet if ALLOW
            cost_usd = result.get("_meta_cost_usd", 0.0)
            duration = result.get("duration", 0) if result.get("decision") == "ALLOW" else 0

            # Update data
            DataManager.update_profile(result.get("learned_topic"), result.get("new_project"))

            # Save logs (costs will be updated if allow)
            costs = CostTracker.calculate_session(cost_usd, duration)
            DataManager.save_logs(text, result, costs)

            # Update UI in main thread
            self.root.after(0, self.update_ui, result, costs)

        except Exception as e:
             self.root.after(0, lambda: messagebox.showerror("Error", f"Backend Error: {e}"))
             self.root.after(0, lambda: self.send_btn.config(state='normal', text="发送请求"))

    def update_ui(self, result, costs):
        self.log_msg("AI", result.get("message", ""), "ai")
        cost_str = f"本次会话成本: {Config.CURRENCY_SYMBOL}{costs['total']:.2f}"
        self.log_msg("SYSTEM", cost_str, "sys")
        self.refresh_cost_display()

        decision = result.get("decision", "BLOCK")

        if decision == "ALLOW":
            self.pending_duration = result.get("duration", 15)
            # Show "Start Deep Work" button
            self.send_btn.config(state='normal', text="开启专注模式", bg="#198754", command=self.start_deep_work)
            self.log_msg("SYSTEM", f"已批准。点击[开启专注模式]以解锁网络 {self.pending_duration} 分钟并最小化。", "sys")
        else:
            # Reset button
            self.send_btn.config(state='normal', text="发送请求", bg="#0e639c", command=self.send_request)

            if decision == "ACK" and result.get("learned_topic"):
                messagebox.showinfo("知识录入", f"已掌握技能: {result['learned_topic']}")

    def refresh_cost_display(self):
        total = CostTracker.get_weekly_total()
        color = "#00ff00" if total < 50 else "#ffaa00" if total < 200 else "#ff0000"
        self.lbl_cost.config(text=f"本周累计消耗: {Config.CURRENCY_SYMBOL}{total:.2f}", fg=color)

    def start_deep_work(self):
        """Called when user clicks 'Start' after approval."""
        self.send_btn.config(state='disabled', text="专注中...")

        # 1. Unlock Network
        NetworkLock.toggle_lock(False)
        self.lbl_lock.config(text=f"UNLOCKED ({self.pending_duration}m)", fg="yellow")

        # 2. Hide Window & Show Tray
        self.root.withdraw()
        self.tray = TrayHandler(
            restore_callback=self.restore_from_tray,
            exit_callback=self.exit_app
        )
        self.tray.run_detached()

        # 3. Start Timer
        self.is_timer_running = True
        self.timer_thread = threading.Thread(target=self.run_timer, args=(self.pending_duration,))
        self.timer_thread.daemon = True
        self.timer_thread.start()

    def run_timer(self, minutes):
        seconds = minutes * 60
        while seconds > 0 and self.is_timer_running:
            time.sleep(1)
            seconds -= 1

        if self.is_timer_running:
            # Time expired naturally
            self.root.after(0, self.on_timer_expire)

    def on_timer_expire(self):
        self.is_timer_running = False
        NetworkLock.toggle_lock(True)

        # Stop tray and restore window
        if self.tray:
            self.tray.stop()
            self.tray = None

        self.root.deiconify()
        self.lbl_lock.config(text="Network: LOCKED", fg="#00ff00")
        self.log_msg("SYSTEM", "时间到。网络已锁定。", "sys")

        # Reset button
        self.send_btn.config(state='normal', text="发送请求", bg="#0e639c", command=self.send_request)
        messagebox.showinfo("Deep Work", "专注时间结束，网络已重新锁定。")

    def restore_from_tray(self):
        """Called from Tray Menu 'Show Window'."""
        # Note: Timer continues running
        self.root.after(0, self.root.deiconify)
        self.tray = None # Tray thread stops automatically when stop() called

    def exit_app(self):
        """Called from Tray Menu 'Exit'."""
        self.is_timer_running = False
        NetworkLock.toggle_lock(True)
        self.root.after(0, self.root.quit)

try:
    if __name__ == "__main__":
        # Admin check
        if os.name == 'nt':
            if not ctypes.windll.shell32.IsUserAnAdmin():
                # Re-run with admin rights
                try:
                    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
                except Exception as e:
                    show_crash_message(f"Failed to elevate privileges:\n{e}")
                sys.exit()

        # Start App
        try:
            root = tk.Tk()
            app = AppGUI(root)
            root.mainloop()
        except Exception as e:
            # Catch errors during mainloop
            show_crash_message(f"Runtime Error:\n{str(e)}\n\n{traceback.format_exc()}")

except Exception as e:
    # Catch any other top-level errors
    show_crash_message(f"Fatal Error:\n{str(e)}\n\n{traceback.format_exc()}")
