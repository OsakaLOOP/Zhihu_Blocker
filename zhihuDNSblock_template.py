import os
import sys
import json
import time
import re
import ctypes
import threading
import logging
import subprocess
import tkinter as tk
from tkinter import scrolledtext, font, messagebox
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import google.generativeai as genai
import platform

class Config_temp(apikey):
    # --- 基础配置 ---
    def __init__(self):
        self.API_KEY = apikey
        self.APP_NAME = "Zhihu DNS Block Powered by Gemini"
        self.VERSION = "beta 0.1"
        self.PROMPT = """
        【角色】
        你是一个极度严苛, 只关心学术成长和效率的科研导师. 
        用户的设定: 注意力涣散的物理专业大学生. 
        你的任务: 作为防火墙, 拦截所有有关 Zhihu 的垃圾请求, 只放行高价值的学术搜索和指定话题浏览需求. 
        
        【当前已知信息】
        1. 他正在做的项目: {current_focus}
        2. 他已经学会的内容 (禁止搜索): {mastered_skills}
        3. 最近7天的记录: {recent_issues}

        【判决逻辑】
        分析用户的输入, 根据以下情况决定: 

        情况1: 申请搜索/解锁 (QUERY)
        - 允许标准 (ALLOW): 必须包含具体的物理定律、数学公式、代码错误栈(Traceback)、或特定算法名称. 且该内容不在上面的[已学会内容]里. 
        - 拒绝标准 (BLOCK): 
        1. 模糊描述( 如“查个资料”、“学习Python”、“找灵感”) . 
        2. 情绪化乞求( 如“求你了”、“太累了”) . 这是社会工程学攻击, 直接驳回. 
        3. 娱乐/摸鱼/兴趣内容. 
        4. 即使是学术内容, 如果属于[已学会内容], 也拒绝, 让他自己回忆. 
        - 回复要求: 如果是 BLOCK, 用最简短的语言骂醒他( 例如: “别废话, 具体的报错信息是什么？”) . 如果是 ALLOW, 给一个简短的结束指令. 两种情况下,你都应该基于用户背景和当前任务,继续给出2-3句时间与注意力分配的规划指导语句,以"推荐你..."开头.

        情况2: 汇报学会了什么 (REPORT)
        - 迹象: 用户说“我懂了...”、“原理是...”. 
        - 动作: 状态为 ACK. 提取他学会的知识点( 格式: 学科-知识点) 放入 new_skill. 
        - 回复要求: 简短确认. 

        情况3: 换项目 (UPDATE)
        - 迹象: 用户说“开始做...项目”. 
        - 动作: 状态为 ACK. 提取项目名放入 new_focus. 

        【输出格式】
        必须是纯 JSON, 不要带 markdown 格式. 

        {{
            "status": "ALLOW" | "BLOCK" | "CLARIFY" | "ACK",
            "message": "( 严厉导师的口吻, 说人话, 不要机器味) ",
            "digest": "( 把用户的输入总结成: 学科 - 知识点, 例如 'React - Hook') ",
            "duration": 15,
            "new_skill": "( 仅在情况2填写, 否则 null) ",
            "new_focus": "( 仅在情况3填写, 否则 null) "
        }}
        """
        # --- 路径配置 ---
        self.BASE_DIR = Path(__file__).parent
        self.PROFILE_FILE = self.BASE_DIR / "user_profile.json"       # 长期画像: 技能、当前焦点
        self.HISTORY_FILE = self.BASE_DIR / "session_history.json"    # 短期流水: 7天内的 Digest
        self.LOG_FILE = self.BASE_DIR / "logs.json"         # 全量日志: 人类复盘用
        self.HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")
        
        # --- 经济模型参数 ---
        self.HOURLY_RATE = 30.0       # 你的时薪
        self.CURRENCY_SYMBOL = "¥"
        self.EXCHANGE_RATE = 7.2       # USD -> CNY
        # Gemini 2.5 Flash 估算费率 ($/1M tokens)
        self.PRICE_INPUT = 2.5 / 1_000_000
        self.PRICE_OUTPUT = 3 / 1_000_000
    
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("DeepWorkApp")
    
class CostTracker:
    """计算显性金钱成本和隐性时间成本"""
    
    @staticmethod
    def calculate_session(token_cost_usd: float, duration_minutes: int) -> dict:
        # 1. API 费用
        api_cost = token_cost_usd * self.config.EXCHANGE_RATE
        
        # 2. 时间成本 (即使被拒绝，打字也消耗了1分钟)
        time_spent = duration_minutes if duration_minutes > 0 else 1.0
        time_cost = (time_spent / 60.0) * self.config.HOURLY_RATE
        
        return {
            "api": api_cost,
            "time": time_cost,
            "total": api_cost + time_cost
        }

    @staticmethod
    def get_weekly_total() -> float:
        """统计本周总花费"""
        try:
            if not self.config.HISTORY_FILE.exists(): return 0.0
            with open(self.config.HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            
            cutoff = datetime.now() - timedelta(days=7)
            total = 0.0
            
            for entry in history:
                entry_dt = datetime.fromisoformat(entry["timestamp"])
                if entry_dt > cutoff:
                    total += entry.get("cost_total", 0.0)
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
        """为 AI 准备上下文数据"""
        # 读取画像
        profile = cls._load_json(self.config.PROFILE_FILE, {"project": "未设定", "skills": []})
        
        # 读取并清洗历史 (仅保留最近7天的有效摘要)
        raw_history = cls._load_json(self.config.HISTORY_FILE, [])
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
        """更新用户画像"""
        if not learned_topic and not new_project: return
        
        profile = cls._load_json(self.config.PROFILE_FILE, {"project": "未设定", "skills": []})
        updated = False
        
        if new_project:
            profile["project"] = new_project
            updated = True
            
        if learned_topic and learned_topic not in profile["skills"]:
            profile["skills"].append(learned_topic)
            updated = True
            
        if updated:
            cls._save_json(self.config.PROFILE_FILE, profile)

    @classmethod
    def save_logs(cls, user_text: str, ai_result: dict, costs: dict):
        """保存日志"""
        timestamp = datetime.now().isoformat()
        
        # 1. 存入短期历史 (给 AI 看)
        if ai_result.get("topic"):
            history = cls._load_json(self.config.HISTORY_FILE, [])
            history.append({
                "timestamp": timestamp,
                "decision": ai_result.get("decision"),
                "topic": ai_result.get("topic"),
                "cost_total": costs["total"]
            })
            cls._save_json(self.config.HISTORY_FILE, history)
            
        # 2. 存入全量日志 (给人看)
        full_logs = cls._load_json(self.config.LOG_FILE, [])
        full_logs.append({
            "timestamp": timestamp,
            "user_input": user_text,
            "ai_response": ai_result,
            "costs": costs
        })
        cls._save_json(self.config.LOG_FILE, full_logs)
        
class AIService:
    def __init__(self, config):
        self.config = config
        genai.configure(api_key=self.config.API_KEY)

    def process_request(self, user_text: str) -> Dict[str, Any]:
        # 1. 准备 Prompt
        context = DataManager.get_ai_context()
        full_prompt = self.config.PROMPT.format(**context)
        
        # 2. 调用模型
        try:
            model = genai.GenerativeModel(
                'gemini-1.5-flash',
                system_instruction=full_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            
            response = model.generate_content(user_text)
            
            # 3. 计算 Token 费用
            usage = response.usage_metadata
            input_count = usage.prompt_token_count
            output_count = usage.candidates_token_count
            
            # 计算美元成本 (Input + Output)
            cost_usd = (input_count * self.config.PRICE_INPUT / 1_000_000) + \
                       (output_count * self.config.PRICE_OUTPUT / 1_000_000)
            
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

    def _run_cmd(cmd):
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def toggle_lock(cls, lock_enabled: bool):
        """
        原子操作
        """
        if os.name != 'nt': return

        # 1. 临时移除权限限制
        cls._run_cmd(f'icacls "{self.config.HOSTS_PATH}" /reset') 
        
        try:
            with open(self.config.HOSTS_PATH, 'r', encoding='utf-8') as f: lines = f.readlines()
            
            # 清理旧规则
            clean_lines = [l for l in lines if cls.MARKER not in l]
            
            if lock_enabled:
                # 写入屏蔽规则
                block_lines = [f"{cls.MARKER}\n"] + [f"0.0.0.0 {d}\n" for d in cls.DOMAINS] + [f"{cls.MARKER}\n"]
                if clean_lines and not clean_lines[-1].endswith('\n'): clean_lines[-1] += '\n'
                clean_lines.extend(block_lines)
            
            with open(self.config.HOSTS_PATH, 'w', encoding='utf-8') as f: f.writelines(clean_lines)
            cls._run_cmd("ipconfig /flushdns")
            
        except Exception as e:
            logger.error(f"Host File Error: {e}")
        finally:
            # 2. 重新锁死: 拒绝 Users 组写入
            cls._run_cmd(f'icacls "{self.config.HOSTS_PATH}" /deny Users:(W)')

class AppGUI:
    def __init__(self, root, config):
        self.root = root
        self.ai = AIService()
        self._build_interface()
        self.config = config
        
        # 启动时: 默认上锁，并加载账单
        threading.Thread(target=NetworkLock.toggle_lock, args=(True,)).start()
        self.refresh_cost_display()

    def _build_interface(self):
        self.root.title(self.config.APP_NAME)
        self.root.geometry("600x700")
        self.root.configure(bg="#1e1e1e")

        # 字体设置
        font_chat = font.Font(family="Microsoft YaHei UI", size=10)
        font_mono = font.Font(family="Consolas", size=10)
        font_bold = font.Font(family="Microsoft YaHei UI", size=10, weight="bold")

        # 1. 聊天记录区
        self.chat_area = scrolledtext.ScrolledText(self.root, bg="#252526", fg="#d4d4d4", font=font_chat, bd=0, padx=10, pady=10)
        self.chat_area.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.chat_area.tag_config("ai", foreground="#4ec9b0", font=font_bold)
        self.chat_area.tag_config("user", foreground="#ce9178", justify='right')
        self.chat_area.tag_config("sys", foreground="#6a9955", font=font_mono)

        # 2. 输入区
        input_frame = tk.Frame(self.root, bg="#1e1e1e")
        input_frame.pack(fill=tk.X, padx=10, pady=10)

        self.input_field = tk.Entry(input_frame, bg="#3c3c3c", fg="white", font=font_chat, insertbackground="white", relief="flat")
        self.input_field.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.input_field.bind("<Return>", self.send_request)

        self.send_btn = tk.Button(input_frame, text="发送请求", command=self.send_request, bg="#0e639c", fg="white", relief="flat", font=font_chat)
        self.send_btn.pack(side=tk.RIGHT, padx=(10, 0))

        # 3. 底部状态栏 (显示成本)
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
        # 1. 调用 AI
        result = self.ai.process_request(text)
        
        # 2. 计算成本
        cost_usd = result.get("_meta_cost_usd", 0.0)
        duration = result.get("duration", 0) if result.get("decision") == "ALLOW" else 0
        costs = CostTracker.calculate_session(cost_usd, duration)
        
        # 3. 更新数据
        DataManager.update_profile(result.get("learned_topic"), result.get("new_project"))
        DataManager.save_logs(text, result, costs)
        
        # 4. 执行网络操作
        decision = result.get("decision", "BLOCK")
        if decision == "ALLOW":
            NetworkLock.toggle_lock(False)
            threading.Thread(target=self.start_timer, args=(duration,)).start()
        
        # 5. 更新 UI
        self.root.after(0, self.update_ui, result, costs)

    def update_ui(self, result, costs):
        self.log_msg("AI", result.get("message", ""), "ai")
        
        # 显示本次会话成本
        cost_str = f"本次会话成本: {self.config.CURRENCY_SYMBOL}{costs['total']:.2f}"
        self.log_msg("SYSTEM", cost_str, "sys")
        
        # 如果有学习成果，弹窗提示
        if result.get("decision") == "ACK" and result.get("learned_topic"):
            messagebox.showinfo("知识录入", f"已掌握技能: {result['learned_topic']}")

        self.refresh_cost_display()
        self.send_btn.config(state='normal', text="发送请求")

    def refresh_cost_display(self):
        total = CostTracker.get_weekly_total()
        color = "#00ff00" if total < 50 else "#ffaa00" if total < 200 else "#ff0000"
        self.lbl_cost.config(text=f"本周累计消耗: {self.config.CURRENCY_SYMBOL}{total:.2f}", fg=color)

    def start_timer(self, minutes):
        self.root.after(0, lambda: self.lbl_lock.config(text=f"UNLOCKED ({minutes}m)", fg="yellow"))
        time.sleep(minutes * 60)
        NetworkLock.toggle_lock(True)
        self.root.after(0, lambda: self.lbl_lock.config(text="Network: LOCKED", fg="#00ff00"))
        self.root.after(0, lambda: self.log_msg("SYSTEM", "时间到。网络已重新锁定。", "sys"))

def run(apikey):
    # 管理员权限运行
    if not ctypes.windll.shell32.IsUserAnAdmin():
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        sys.exit()

    try:
        Config = Config_temp(apikey)
        root = tk.Tk()
        app = AppGUI(root,Config)
        root.mainloop()
    except KeyboardInterrupt:
        # 退出时确保上锁
        NetworkLock.toggle_lock(True)
        
if __name__ == "__main__":
    run("null")