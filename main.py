import discord
from discord.ext import commands
import threading
import time
import json
import uuid
import ssl
import paho.mqtt.client as mqtt
import warnings
import requests
import re
import random
import gc
import asyncio
import os
import hashlib
import string
import psutil
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional, List

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ============= PROXY FAILOVER =============
PROXY_FILE = input("Nhập đường dẫn file proxy list (bỏ trống nếu không): ").strip()
proxy_list = []
if PROXY_FILE and os.path.isfile(PROXY_FILE):
    with open(PROXY_FILE, 'r', encoding='utf-8') as f:
        proxy_list = [line.strip() for line in f if line.strip()]
    print(f"[!] Đã tải {len(proxy_list)} proxy")
else:
    print("[!] Không dùng proxy")

proxy_index = 0
proxy_lock = threading.Lock()

def get_next_proxy():
    if not proxy_list:
        return None
    with proxy_lock:
        p = proxy_list[proxy_index % len(proxy_list)]
        proxy_index += 1
        return p

# ============= BOT CONFIG =============
BOT_TOKEN = input("Token bot: ").strip()
ADMIN_ID = int(input("ID Admin: ").strip())
PREFIX = input("Prefix: ").strip()

# Lưu nội dung file mặc định
default_full_text = ""       # toàn bộ nội dung file (dùng cho ngonmess)
default_lines = []           # list các dòng (dùng cho treopoll, nhaytag)
default_poll_question = ""   # câu hỏi poll (dòng 1)
default_poll_options = []    # 2 lựa chọn (dòng 2, 3)

active_tabs = {}
BOT_START_TIME = time.time()

# ============= AUTO RAM CLEANER =============
def auto_clean_ram():
    while True:
        time.sleep(1800)
        try:
            mem = psutil.virtual_memory()
            if mem.percent > 70:
                gc.collect()
                print(f"[RAM] Đã dọn, sử dụng: {mem.percent}%")
        except:
            pass
threading.Thread(target=auto_clean_ram, daemon=True).start()

# ============= UTILITY =============
def generate_offline_threading_id():
    return str(int(time.time() * 1000)) + str(random.randint(1000, 9999))

def json_minimal(data):
    return json.dumps(data, separators=(",", ":"))

def parse_cookie_string(cookie_string):
    cookies = {}
    for part in cookie_string.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            cookies[key] = value
    return cookies

def generate_session_id():
    return hashlib.md5(str(time.time()).encode()).hexdigest()

def generate_client_id():
    return str(random.randint(10**14, 10**15 - 1))

def get_uid_from_cookie(cookie):
    match = re.search(r'c_user=(\d+)', cookie)
    return match.group(1) if match else None

# ============= HTTP (cho lệnh idbox) =============
class FacebookSession:
    def __init__(self, cookie, proxy_dict=None):
        self.cookie = cookie
        self.uid = self.get_uid()
        self.proxy_dict = proxy_dict
        self.fb_dtsg, self.jazoest = self.init_params()

    def get_uid(self):
        try:
            return re.search(r"c_user=(\d+)", self.cookie).group(1)
        except:
            raise Exception("Cookie không hợp lệ (thiếu c_user)")

    def init_params(self):
        headers = {'Cookie': self.cookie, 'User-Agent': 'Mozilla/5.0'}
        try:
            response = requests.get('https://www.facebook.com', headers=headers, proxies=self.proxy_dict, timeout=15)
            fb_dtsg_match = re.search(r'"token":"(.*?)"', response.text)
            jazoest_match = re.search(r'name="jazoest" value="(\d+)"', response.text)
            if not fb_dtsg_match:
                response = requests.get('https://mbasic.facebook.com', headers=headers, proxies=self.proxy_dict, timeout=15)
                fb_dtsg_match = re.search(r'name="fb_dtsg" value="(.*?)"', response.text)
                jazoest_match = re.search(r'name="jazoest" value="(\d+)"', response.text)
            if fb_dtsg_match:
                fb_dtsg = fb_dtsg_match.group(1)
                jazoest = jazoest_match.group(1) if jazoest_match else "22036"
                return fb_dtsg, jazoest
            raise Exception("Không thể lấy fb_dtsg")
        except Exception as e:
            raise Exception(f"Lỗi lấy fb_dtsg: {str(e)}")

def get_thread_list(cookie, proxy_dict=None, limit=500):
    try:
        session = FacebookSession(cookie, proxy_dict)
    except Exception as e:
        return {"error": str(e)}
    form_data = {
        "av": session.uid, "__user": session.uid, "fb_dtsg": session.fb_dtsg, "jazoest": session.jazoest,
        "__a": "1", "__req": "1b", "__rev": "1015919737", "__comet_req": "15",
        "__spin_r": "999999999", "__spin_b": "trunk", "__spin_t": str(int(time.time())),
        "queries": json.dumps({
            "o0": {
                "doc_id": "3336396659757871",
                "query_params": {
                    "limit": limit, "before": None, "tags": ["INBOX"],
                    "includeDeliveryReceipts": False, "includeSeqID": True
                }
            }
        })
    }
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}
    try:
        response = requests.post("https://www.facebook.com/api/graphqlbatch/", data=form_data, headers=headers, proxies=proxy_dict, timeout=15)
        data_raw = response.text.split('{"successful_results"')[0]
        data = json.loads(data_raw)
        threads = data["o0"]["data"]["viewer"]["message_threads"]["nodes"]
        result = []
        for thread in threads:
            if thread.get("thread_key", {}).get("thread_fbid"):
                result.append({"thread_id": thread["thread_key"]["thread_fbid"], "thread_name": thread.get("name") or "Không có tên"})
        return result
    except Exception as e:
        return {"error": f"Lỗi lấy danh sách box: {e}"}

# ============= MQTT CLIENT WRAPPER (FAILOVER) =============
class MQTTClientWrapper:
    def __init__(self, cookie):
        self.cookie = cookie
        self.uid = None
        self.fb_dtsg = None
        self.jazoest = None
        self.mqtt = None
        self.connected = False
        self.ws_req_number = 0
        self.ws_task_number = 0

    def _refresh_tokens_with_proxy(self, proxy_dict):
        sess = FacebookSession(self.cookie, proxy_dict)
        self.uid = sess.uid
        self.fb_dtsg = sess.fb_dtsg
        self.jazoest = sess.jazoest
        return True

    def connect(self):
        max_retries = len(proxy_list) if proxy_list else 1
        for attempt in range(max_retries):
            proxy = get_next_proxy() if proxy_list else None
            proxy_dict = {"http": proxy, "https": proxy} if proxy else None
            try:
                if not self.fb_dtsg or attempt > 0:
                    self._refresh_tokens_with_proxy(proxy_dict)

                session_id = generate_session_id()
                client_id = generate_client_id()
                user_info = {
                    "u": self.uid, "s": session_id, "chat_on": True, "fg": False, "d": client_id,
                    "ct": "websocket", "aid": "219994525426954", "mqtt_sid": "", "cp": 3, "ecp": 10,
                    "st": [], "pm": [], "dc": "", "no_auto_fg": True, "gas": None, "pack": []
                }
                cookie_str = "; ".join([f"{k}={v}" for k, v in parse_cookie_string(self.cookie).items()])
                self.mqtt = mqtt.Client(client_id="mqttwsclient", clean_session=True, protocol=mqtt.MQTTv31, transport="websockets")
                self.mqtt.tls_set(certfile=None, keyfile=None, cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLSv1_2)
                self.mqtt.tls_insecure_set(True)
                self.mqtt.username_pw_set(username=json_minimal(user_info))
                self.mqtt.ws_set_options(path="/chat", headers={
                    "Cookie": cookie_str, "Origin": "https://www.facebook.com",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://www.facebook.com/", "Host": "edge-chat.facebook.com"
                })
                connected_event = threading.Event()
                def on_connect(client, userdata, flags, rc):
                    self.connected = (rc == 0)
                    connected_event.set()
                self.mqtt.on_connect = on_connect
                self.mqtt.connect("edge-chat.facebook.com", 443, 10)
                self.mqtt.loop_start()
                if connected_event.wait(timeout=10) and self.connected:
                    print(f"[MQTT] Kết nối thành công (proxy: {proxy})")
                    return True
                else:
                    raise Exception("Kết nối timeout")
            except Exception as e:
                print(f"[MQTT] Lỗi với proxy {proxy}: {e}")
                if self.mqtt:
                    self.mqtt.loop_stop()
                    self.mqtt.disconnect()
                self.connected = False
                self.fb_dtsg = None
                continue
        return False

    def disconnect(self):
        if self.mqtt and self.connected:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
            self.connected = False

    def _publish(self, payload, topic="/ls_req", qos=1):
        if not self.connected and not self.connect():
            return False
        try:
            self.mqtt.publish(topic, payload, qos)
            return True
        except:
            self.connected = False
            return False

    def send_message(self, thread_id, message):
        self.ws_req_number += 1
        self.ws_task_number += 1
        task_payload = {
            "thread_key": thread_id, "message_text": message,
            "offline_threading_id": generate_offline_threading_id(), "source": "source:chat:web"
        }
        task = {"label": "46", "payload": json_minimal(task_payload), "queue_name": "thread_message", "task_id": self.ws_task_number}
        content = {
            "app_id": "2220391788200892",
            "payload": json_minimal({"data_trace_id": None, "epoch_id": int(generate_offline_threading_id()), "tasks": [task], "version_id": "25095469420099952"}),
            "request_id": self.ws_req_number, "type": 3
        }
        return self._publish(json_minimal(content))

    def send_typing(self, thread_id, is_typing):
        self.ws_req_number += 1
        task_payload = {"thread_key": thread_id, "is_group_thread": 1, "is_typing": 1 if is_typing else 0, "attribution": 0}
        content = {
            "app_id": "2220391788200892",
            "payload": json.dumps({"label": "3", "payload": json.dumps(task_payload, separators=(",", ":")), "version": "25393437286970779"}, separators=(",", ":")),
            "request_id": self.ws_req_number, "type": 4
        }
        return self._publish(json.dumps(content, separators=(",", ":")))

    def send_message_with_mention(self, thread_id, message, mentioned_uids):
        self.ws_req_number += 1
        self.ws_task_number += 1
        body = message
        profile_xmd = []
        for uid in mentioned_uids:
            tag = f"@{uid}"
            offset = len(body)
            body += f" {tag}"
            profile_xmd.append({"id": uid, "offset": offset + 1, "length": len(tag), "type": "p"})
        task_payload = {
            "thread_key": thread_id, "message_text": body, "offline_threading_id": generate_offline_threading_id(),
            "source": "source:chat:web", "profile_xmd": profile_xmd
        }
        task = {"label": "46", "payload": json_minimal(task_payload), "queue_name": "thread_message", "task_id": self.ws_task_number}
        content = {
            "app_id": "2220391788200892",
            "payload": json_minimal({"data_trace_id": None, "epoch_id": int(generate_offline_threading_id()), "tasks": [task], "version_id": "25095469420099952"}),
            "request_id": self.ws_req_number, "type": 3
        }
        return self._publish(json_minimal(content))

    def send_poll(self, thread_id, question, options):
        self.ws_req_number += 1
        task_payload = {"question_text": question, "thread_key": int(thread_id), "options": options, "sync_group": 1}
        task = {"label": "163", "payload": json.dumps(task_payload, separators=(",", ":")), "queue_name": "poll_creation", "task_id": random.randint(1, 10000)}
        content = {
            "app_id": "2220391788200892",
            "payload": json.dumps({"epoch_id": int(generate_offline_threading_id()), "tasks": [task], "version_id": "7158486590867448"}, separators=(",", ":")),
            "request_id": random.randint(1, 5000), "type": 3
        }
        return self._publish(json.dumps(content, separators=(",", ":")))

    def set_theme(self, thread_id, theme_id):
        self.ws_req_number += 1
        self.ws_task_number += 1
        task_payload = {"thread_key": thread_id, "theme_fbid": theme_id, "source": None, "sync_group": 1, "payload": None}
        task = {"failure_count": None, "label": "43", "payload": json_minimal(task_payload), "queue_name": "thread_theme", "task_id": self.ws_task_number}
        content = {
            "app_id": "2220391788200892",
            "payload": json_minimal({"data_trace_id": None, "epoch_id": int(generate_offline_threading_id()), "tasks": [task], "version_id": "25095469420099952"}),
            "request_id": self.ws_req_number, "type": 3
        }
        return self._publish(json_minimal(content))

# ============= DANH SÁCH THEMES =============
THEMES = [
    {"id": "3650637715209675", "name": "Besties"},
    {"id": "769656934577391", "name": "Women's History Month"},
    {"id": "702099018755409", "name": "Dune: Part Two"},
    {"id": "1480404512543552", "name": "Avatar: The Last Airbender"},
    {"id": "952656233130616", "name": "J.Lo"},
    {"id": "741311439775765", "name": "Love"},
    {"id": "215565958307259", "name": "Bob Marley: One Love"},
    {"id": "194982117007866", "name": "Football"},
    {"id": "1743641112805218", "name": "Soccer"},
    {"id": "730357905262632", "name": "Mean Girls"},
    {"id": "1270466356981452", "name": "Wonka"},
    {"id": "704702021720552", "name": "Pizza"},
    {"id": "1013083536414851", "name": "Wish"},
    {"id": "359537246600743", "name": "Trolls"},
    {"id": "173976782455615", "name": "The Marvels"},
    {"id": "2317258455139234", "name": "One Piece"},
    {"id": "6685081604943977", "name": "1989"},
    {"id": "1508524016651271", "name": "Avocado"},
    {"id": "265997946276694", "name": "Loki Season 2"},
    {"id": "6584393768293861", "name": "olivia rodrigo"},
    {"id": "845097890371902", "name": "Baseball"},
    {"id": "292955489929680", "name": "Lollipop"},
    {"id": "976389323536938", "name": "Loops"},
    {"id": "810978360551741", "name": "Parenthood"},
    {"id": "195296273246380", "name": "Bubble Tea"},
    {"id": "6026716157422736", "name": "Basketball"},
    {"id": "693996545771691", "name": "Elephants & Flowers"},
    {"id": "390127158985345", "name": "Chill"},
    {"id": "365557122117011", "name": "Support"},
    {"id": "339021464972092", "name": "Music"},
    {"id": "1060619084701625", "name": "Lo-Fi"},
    {"id": "3190514984517598", "name": "Sky"},
    {"id": "627144732056021", "name": "Celebration"},
    {"id": "275041734441112", "name": "Care"},
    {"id": "3082966625307060", "name": "Astrology"},
    {"id": "539927563794799", "name": "Cottagecore"},
    {"id": "527564631955494", "name": "Ocean"},
    {"id": "230032715012014", "name": "Tie-Dye"},
    {"id": "788274591712841", "name": "Monochrome"},
    {"id": "3259963564026002", "name": "Default"},
    {"id": "724096885023603", "name": "Berry"},
    {"id": "624266884847972", "name": "Candy"},
    {"id": "273728810607574", "name": "Unicorn"},
    {"id": "262191918210707", "name": "Tropical"},
    {"id": "2533652183614000", "name": "Maple"},
    {"id": "909695489504566", "name": "Sushi"},
    {"id": "582065306070020", "name": "Rocket"},
    {"id": "557344741607350", "name": "Citrus"},
    {"id": "280333826736184", "name": "Lollipop"},
    {"id": "271607034185782", "name": "Shadow"},
    {"id": "1257453361255152", "name": "Rose"},
    {"id": "571193503540759", "name": "Lavender"},
    {"id": "2873642949430623", "name": "Tulip"},
    {"id": "3273938616164733", "name": "Classic"},
    {"id": "403422283881973", "name": "Apple"},
    {"id": "3022526817824329", "name": "Peach"},
    {"id": "672058580051520", "name": "Honey"},
    {"id": "3151463484918004", "name": "Kiwi"},
    {"id": "736591620215564", "name": "Ocean"},
    {"id": "193497045377796", "name": "Grape"}
]

# ============= LOOPS CHO TỪNG LỆNH =============
def ngonmess_loop(cookie, idbox, delay, stop_event):
    global default_full_text
    if not default_full_text:
        return
    mqtt = MQTTClientWrapper(cookie)
    if not mqtt.connect():
        return
    while not stop_event.is_set():
        mqtt.send_message(idbox, default_full_text)
        time.sleep(delay)
    mqtt.disconnect()

def treopoll_loop(cookie, idbox, delay, stop_event):
    global default_poll_question, default_poll_options
    if not default_poll_question or len(default_poll_options) < 2:
        return
    mqtt = MQTTClientWrapper(cookie)
    if not mqtt.connect():
        return
    while not stop_event.is_set():
        mqtt.send_typing(idbox, True)
        time.sleep(random.uniform(2, 5))
        mqtt.send_poll(idbox, default_poll_question, default_poll_options)
        mqtt.send_typing(idbox, False)
        time.sleep(delay)
    mqtt.disconnect()

def nhaytag_loop(cookie, idbox, uids, delay, stop_event):
    global default_lines
    if not default_lines:
        return
    mqtt = MQTTClientWrapper(cookie)
    if not mqtt.connect():
        return
    idx = 0
    while not stop_event.is_set():
        msg = default_lines[idx % len(default_lines)]
        idx += 1
        mqtt.send_typing(idbox, True)
        time.sleep(random.uniform(2, 5))
        mqtt.send_message_with_mention(idbox, msg, uids)
        mqtt.send_typing(idbox, False)
        time.sleep(delay)
    mqtt.disconnect()

def nhay_loop(cookie, idbox, delay, stop_event):
    file_path = "nhay.txt"
    if not os.path.isfile(file_path):
        return
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]
    if not lines:
        return
    mqtt = MQTTClientWrapper(cookie)
    if not mqtt.connect():
        return
    idx = 0
    while not stop_event.is_set():
        msg = lines[idx % len(lines)]
        idx += 1
        mqtt.send_typing(idbox, True)
        time.sleep(random.uniform(2, 5))
        mqtt.send_message(idbox, msg)
        mqtt.send_typing(idbox, False)
        time.sleep(delay)
    mqtt.disconnect()

def setnen_loop(cookie, idbox, delay, stop_event):
    mqtt = MQTTClientWrapper(cookie)
    if not mqtt.connect():
        return
    while not stop_event.is_set():
        theme = random.choice(THEMES)
        mqtt.set_theme(idbox, theme["id"])
        time.sleep(delay)
    mqtt.disconnect()

# ============= DISCORD BOT =============
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

def runtime(start):
    sec = int(time.time() - start)
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{d} ngày {h:02} giờ {m:02} phút {s:02} giây"

@bot.event
async def on_ready():
    print(f"Bot đã sẵn sàng: {bot.user}")

@bot.command()
async def menu(ctx):
    if ctx.author.id != ADMIN_ID: return
    embed = discord.Embed(title="MENU BOT (MQTT + Proxy Failover)", color=0xB8F0FF)
    embed.add_field(name="Lệnh", value=f"""
`{PREFIX}menu` - Menu
`{PREFIX}setfile` - Upload file .txt (dùng chung)
`{PREFIX}idbox <cookie>` - Lấy danh sách box
`{PREFIX}ngonmess <idbox> <cookie> <delay>` - Gửi toàn bộ file (ko typing)
`{PREFIX}treopoll <idbox> <cookie> <delay>` - Poll 3 dòng đầu (có typing)
`{PREFIX}nhaytag <idbox> <cookie> <uid1,uid2,...> <delay>` - Tag từng dòng + typing
`{PREFIX}nhay <idbox> <cookie> <delay>` - Gửi từng dòng từ nhay.txt + typing (ko tag)
`{PREFIX}setnen <idbox> <cookie> <delay>` - Set theme random
`{PREFIX}uptime` - Thời gian bot chạy
`{PREFIX}tab` - Xem danh sách task
`{PREFIX}stop <số thứ tự|all>` - Dừng task theo số thứ tự hoặc all
""", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def setfile(ctx):
    global default_full_text, default_lines, default_poll_question, default_poll_options
    if ctx.author.id != ADMIN_ID: return

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    await ctx.send("📤 Hãy **upload file .txt** hoặc **nhập đường dẫn file** (gửi 'cancel' để hủy):")
    msg = await bot.wait_for("message", check=check)
    if msg.content.lower() == "cancel":
        await ctx.send("Đã hủy.")
        return

    content = None
    if msg.attachments:
        att = msg.attachments[0]
        if not att.filename.endswith('.txt'):
            await ctx.send("❌ Chỉ chấp nhận file .txt")
            return
        content = (await att.read()).decode('utf-8')
    else:
        file_path = msg.content.strip()
        if not os.path.isfile(file_path):
            await ctx.send(f"❌ Không tìm thấy file: {file_path}")
            return
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

    lines = [line.strip() for line in content.split('\n') if line.strip()]
    if not lines:
        await ctx.send("❌ File rỗng")
        return

    default_full_text = content.strip()
    default_lines = lines
    if len(lines) >= 3:
        default_poll_question = lines[0]
        default_poll_options = lines[1:3]
        await ctx.send(f"✅ Đã set file với {len(lines)} dòng.\n📌 ngonmess: gửi toàn bộ file.\n📌 treopoll: dùng 3 dòng đầu (câu hỏi: `{default_poll_question[:50]}...`)\n📌 nhaytag: dùng từng dòng.")
    else:
        default_poll_question = ""
        default_poll_options = []
        await ctx.send(f"✅ Đã set file với {len(lines)} dòng.\n⚠️ treopoll cần ít nhất 3 dòng, hiện tại không đủ.")

@bot.command()
async def idbox(ctx, cookie: str = None):
    if ctx.author.id != ADMIN_ID or not cookie:
        await ctx.send("Cú pháp: `.idbox <cookie>`")
        return
    proxy_dict = {"http": get_next_proxy(), "https": get_next_proxy()} if proxy_list else None
    await ctx.send("🔄 Đang lấy danh sách box...")
    result = get_thread_list(cookie, proxy_dict)
    if isinstance(result, dict) and "error" in result:
        await ctx.send(f"❌ {result['error']}")
        return
    if not result:
        await ctx.send("❌ Không tìm thấy box nào")
        return
    msg = "**📋 Danh sách box:**\n```\n"
    for i, t in enumerate(result, 1):
        msg += f"{i}. {t['thread_name'][:40]} - {t['thread_id']}\n"
    msg += "```"
    await ctx.send(msg[:2000])

@bot.command()
async def ngonmess(ctx, idbox: str = None, cookie: str = None, delay: str = None):
    if ctx.author.id != ADMIN_ID or None in (idbox, cookie, delay):
        await ctx.send("Cú pháp: `.ngonmess <idbox> <cookie> <delay>`")
        return
    if not default_full_text:
        await ctx.send("❌ Chưa có file mặc định. Hãy dùng `.setfile` trước.")
        return
    try:
        delay = float(delay)
    except:
        await ctx.send("Delay phải là số")
        return
    stop = threading.Event()
    threading.Thread(target=ngonmess_loop, args=(cookie, idbox, delay, stop), daemon=True).start()
    active_tabs.setdefault(ctx.author.id, []).append({"type": "ngonmess", "idbox": idbox, "stop": stop, "start": time.time()})
    await ctx.send(f"✅ Bắt đầu ngonmess tới {idbox}, delay {delay}s (gửi toàn bộ file)")

@bot.command()
async def treopoll(ctx, idbox: str = None, cookie: str = None, delay: str = None):
    if ctx.author.id != ADMIN_ID or None in (idbox, cookie, delay):
        await ctx.send("Cú pháp: `.treopoll <idbox> <cookie> <delay>`")
        return
    if not default_poll_question or len(default_poll_options) < 2:
        await ctx.send("❌ File mặc định cần ít nhất 3 dòng để tạo poll. Hãy dùng `.setfile` với file có >=3 dòng.")
        return
    try:
        delay = float(delay)
    except:
        await ctx.send("Delay phải là số")
        return
    stop = threading.Event()
    threading.Thread(target=treopoll_loop, args=(cookie, idbox, delay, stop), daemon=True).start()
    active_tabs.setdefault(ctx.author.id, []).append({"type": "treopoll", "idbox": idbox, "stop": stop, "start": time.time()})
    await ctx.send(f"✅ Bắt đầu treopoll tới {idbox}, delay {delay}s (dùng 3 dòng đầu)")

@bot.command()
async def nhaytag(ctx, idbox: str = None, cookie: str = None, uid_list: str = None, delay: str = None):
    if ctx.author.id != ADMIN_ID or None in (idbox, cookie, uid_list, delay):
        await ctx.send("Cú pháp: `.nhaytag <idbox> <cookie> <uid1,uid2,...> <delay>`")
        return
    if not default_lines:
        await ctx.send("❌ Chưa có file mặc định. Hãy dùng `.setfile` trước.")
        return
    uids = [u.strip() for u in uid_list.split(',') if u.strip()]
    if not uids:
        await ctx.send("❌ Phải có ít nhất 1 uid")
        return
    try:
        delay = float(delay)
    except:
        await ctx.send("Delay phải là số")
        return
    stop = threading.Event()
    threading.Thread(target=nhaytag_loop, args=(cookie, idbox, uids, delay, stop), daemon=True).start()
    active_tabs.setdefault(ctx.author.id, []).append({"type": "nhaytag", "idbox": idbox, "stop": stop, "start": time.time()})
    await ctx.send(f"✅ Bắt đầu nhaytag tới {idbox} với {len(uids)} uid, delay {delay}s")

@bot.command()
async def nhay(ctx, idbox: str = None, cookie: str = None, delay: str = None):
    if ctx.author.id != ADMIN_ID:
        return
    if None in (idbox, cookie, delay):
        await ctx.send("Cú pháp: `.nhay <idbox> <cookie> <delay>`")
        return
    try:
        delay = float(delay)
    except:
        await ctx.send("Delay phải là số")
        return
    if not os.path.isfile("nhay.txt"):
        await ctx.send("❌ Không tìm thấy file `nhay.txt` trong thư mục bot!")
        return
    with open("nhay.txt", 'r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]
    if not lines:
        await ctx.send("❌ File `nhay.txt` rỗng!")
        return
    stop = threading.Event()
    threading.Thread(target=nhay_loop, args=(cookie, idbox, delay, stop), daemon=True).start()
    active_tabs.setdefault(ctx.author.id, []).append({"type": "nhay", "idbox": idbox, "stop": stop, "start": time.time()})
    await ctx.send(f"✅ Bắt đầu nhay tới {idbox}, delay {delay}s (nội dung từ nhay.txt, có typing)")

@bot.command()
async def setnen(ctx, idbox: str = None, cookie: str = None, delay: str = None):
    if ctx.author.id != ADMIN_ID or None in (idbox, cookie, delay):
        await ctx.send("Cú pháp: `.setnen <idbox> <cookie> <delay>`")
        return
    try:
        delay = float(delay)
    except:
        await ctx.send("Delay phải là số")
        return
    stop = threading.Event()
    threading.Thread(target=setnen_loop, args=(cookie, idbox, delay, stop), daemon=True).start()
    active_tabs.setdefault(ctx.author.id, []).append({"type": "setnen", "idbox": idbox, "stop": stop, "start": time.time()})
    await ctx.send(f"✅ Bắt đầu set theme random cho {idbox}, delay {delay}s")

@bot.command()
async def uptime(ctx):
    if ctx.author.id != ADMIN_ID: return
    await ctx.send(f"⏰ Bot đã chạy: `{runtime(BOT_START_TIME)}`")

@bot.command()
async def tab(ctx):
    if ctx.author.id != ADMIN_ID: return
    tabs = active_tabs.get(ctx.author.id, [])
    if not tabs:
        await ctx.send("Không có task nào đang chạy")
        return
    out = ["**📋 Danh sách task đang chạy:**"]
    for i, t in enumerate(tabs, 1):
        out.append(f"{i}. **{t['type']}** - {t['idbox']} - {runtime(t['start'])}")
    await ctx.send("\n".join(out))

@bot.command()
async def stop(ctx, index_str: str = None):
    if ctx.author.id != ADMIN_ID:
        return
    user_tabs = active_tabs.get(ctx.author.id, [])
    if not user_tabs:
        await ctx.send("Không có task nào đang chạy.")
        return

    if index_str is None:
        await ctx.send("❌ Cú pháp: `.stop <số thứ tự>` hoặc `.stop all`\nDùng `.tab` để xem số thứ tự.")
        return

    if index_str.lower() == 'all':
        for tab in list(user_tabs):
            tab["stop"].set()
        active_tabs[ctx.author.id] = []
        await ctx.send(f"✅ Đã dừng tất cả {len(user_tabs)} task.")
        return

    try:
        idx = int(index_str) - 1
        if 0 <= idx < len(user_tabs):
            tab = user_tabs[idx]
            tab["stop"].set()
            active_tabs[ctx.author.id].pop(idx)
            await ctx.send(f"✅ Đã dừng task #{idx+1} ({tab['type']} - {tab['idbox']})")
        else:
            await ctx.send(f"❌ Số thứ tự không hợp lệ (1..{len(user_tabs)})")
    except ValueError:
        await ctx.send("❌ Vui lòng nhập số thứ tự hoặc 'all'")

bot.run(BOT_TOKEN
