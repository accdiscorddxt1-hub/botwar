import discord
from discord.ext import commands
import asyncio
import os
import re
import time
import json
import requests
import random
import base64
import gc
from datetime import datetime
from typing import Dict, Any

# Kiểm tra và import psutil an toàn
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[CẢNH BÁO] psutil không có sẵn, chức năng dọn RAM sẽ bị hạn chế")

# Nhập dữ liệu khi khởi chạy
TOKEN = input("Nhập Token Bot: ")
IDADMIN_GOC = int(input("Nhập ID Admin gốc: "))
PREFIX = input("Nhập Prefix Bot: ")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# RAM lưu trạng thái
admins = [IDADMIN_GOC]
saved_files = {}
running_tasks = {}
task_info = {}
cookie_managers = {}

# Màu cho console
COLOR_ERROR = "\033[91m"
COLOR_SUCCESS = "\033[92m"
COLOR_WARNING = "\033[93m"
COLOR_RESET = "\033[0m"
trang = COLOR_RESET

# Cấu hình dọn RAM - 1 phút 1 lần
RAM_CLEAN_INTERVAL = 60
last_ram_clean = time.time()

def get_ram_usage_mb():
    """Lấy RAM đang dùng (MB)"""
    if PSUTIL_AVAILABLE:
        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024
        except:
            return 0
    return 0

def clean_ram():
    """Dọn RAM"""
    gc.collect()
    gc.collect()
    
    # Xóa task rác
    for task_id in list(running_tasks.keys()):
        try:
            if running_tasks[task_id].done():
                del running_tasks[task_id]
        except:
            pass
    
    for task_id in list(task_info.keys()):
        if task_id not in running_tasks:
            try:
                del task_info[task_id]
            except:
                pass
    
    if PSUTIL_AVAILABLE:
        ram_used = get_ram_usage_mb()
        print(f"{COLOR_SUCCESS}[RAM] Đã dọn, RAM hiện: {ram_used:.2f} MB{trang}")
    else:
        print(f"{COLOR_SUCCESS}[RAM] Đã dọn{trang}")

async def ram_cleaner_loop():
    """Chạy ngầm dọn RAM mỗi 1 phút"""
    await bot.wait_until_ready()
    global last_ram_clean
    while not bot.is_closed():
        try:
            current_time = time.time()
            if current_time - last_ram_clean >= RAM_CLEAN_INTERVAL:
                clean_ram()
                last_ram_clean = current_time
            await asyncio.sleep(10)
        except Exception as e:
            print(f"{COLOR_ERROR}[LỖI] Ram cleaner: {e}{trang}")
            await asyncio.sleep(10)

# Hàm lấy uid từ cookie
def get_uid(cookie):
    try:
        match = re.search(r'c_user=(\d+)', cookie)
        return match.group(1) if match else '0'
    except:
        return '0'

# Class CookieManager quản lý refresh fb_dtsg
class CookieManager:
    def __init__(self, cookie, target_id):
        self.cookie = cookie
        self.target_id = target_id
        self.user_id = None
        self.fb_dtsg = None
        self.jazoest = None
        self.last_refresh = 0
        self.refresh_interval = 300
        
    def init_params(self):
        try:
            response = requests.get(
                f'https://mbasic.facebook.com/privacy/touch/block/confirm/?bid={self.target_id}&ret_cancel&source=profile',
                headers={'cookie': self.cookie, 'user-agent': 'Mozilla/5.0'},
                timeout=30
            )
            fb_dtsg_match = re.search(r'name="fb_dtsg" value="([^"]+)"', response.text)
            jazoest_match = re.search(r'name="jazoest" value="([^"]+)"', response.text)
            
            if fb_dtsg_match and jazoest_match:
                self.fb_dtsg = fb_dtsg_match.group(1)
                self.jazoest = jazoest_match.group(1)
                self.user_id = get_uid(self.cookie)
                self.last_refresh = time.time()
                print(f"{COLOR_SUCCESS}[COOKIE] Đã refresh fb_dtsg cho user {self.user_id}{trang}")
                return True
            return False
        except Exception as e:
            print(f"{COLOR_ERROR}[LỖI] Init params: {str(e)}{trang}")
            return False
    
    def refresh_fb_dtsg(self):
        self.fb_dtsg = None
        try:
            self.init_params()
            return self.fb_dtsg is not None
        except Exception as e:
            print(f"{COLOR_ERROR}[LỖI LÀM MỚI] Cookie {self.user_id}: {str(e)}{trang}")
            return False
    
    def is_valid(self):
        if not self.fb_dtsg or not self.jazoest:
            return False
        if time.time() - self.last_refresh > self.refresh_interval:
            return self.refresh_fb_dtsg()
        return True
    
    def get_fb_dtsg(self):
        if not self.is_valid():
            self.refresh_fb_dtsg()
        return self.fb_dtsg
    
    def get_jazoest(self):
        if not self.is_valid():
            self.refresh_fb_dtsg()
        return self.jazoest

def send_message_with_manager(cm: CookieManager, message_body):
    try:
        fb_dtsg = cm.get_fb_dtsg()
        jazoest = cm.get_jazoest()
        if not fb_dtsg or not jazoest:
            return False
            
        uid = cm.user_id
        timestamp = int(time.time() * 1000)
        data = {
            'thread_fbid': cm.target_id,
            'action_type': 'ma-type:user-generated-message',
            'body': message_body,
            'client': 'mercury',
            'author': f'fbid:{uid}',
            'timestamp': timestamp,
            'source': 'source:chat:web',
            'offline_threading_id': str(timestamp),
            'message_id': str(timestamp),
            'ephemeral_ttl_mode': '',
            '__user': uid,
            '__a': '1',
            '__req': '1b',
            '__rev': '1015919737',
            'fb_dtsg': fb_dtsg,
            'jazoest': jazoest
        }
        headers = {
            'Cookie': cm.cookie,
            'User-Agent': 'Mozilla/5.0',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.facebook.com',
            'Referer': f'https://www.facebook.com/messages/t/{cm.target_id}'
        }
        response = requests.post('https://www.facebook.com/messaging/send/', data=data, headers=headers, timeout=30)
        return response.status_code == 200
    except Exception as e:
        print(f"{COLOR_ERROR}[LỖI GỬI] {str(e)}{trang}")
        return False

# Lệnh addadmin
@bot.command()
async def addadmin(ctx, member: discord.Member):
    if ctx.author.id != IDADMIN_GOC:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    if member.id not in admins:
        admins.append(member.id)
        await ctx.send(f"Đã thêm `{member.name}` vào danh sách admin.")
    else:
        await ctx.send("Người này đã là admin rồi.")

@bot.command()
async def deladmin(ctx, member: discord.Member):
    if ctx.author.id != IDADMIN_GOC:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    if member.id in admins and member.id != IDADMIN_GOC:
        admins.remove(member.id)
        await ctx.send(f"Đã xoá `{member.name}` khỏi danh sách admin.")
        
        to_remove = [task_id for task_id, info in task_info.items() if info.get('admin_id') == member.id]
        for task_id in to_remove:
            if task_id in running_tasks:
                try:
                    running_tasks[task_id].cancel()
                except:
                    pass
                del running_tasks[task_id]
            if task_id in cookie_managers:
                del cookie_managers[task_id]
            if task_id in task_info:
                del task_info[task_id]
        await ctx.send(f"Đã dừng tất cả task do `{member.name}` tạo.")
    else:
        await ctx.send("Không thể xoá admin gốc hoặc người này không phải admin.")

@bot.command()
async def listadmin(ctx):
    msg = "**Danh sách admin hiện tại:**\n"
    for admin_id in admins:
        try:
            user = await bot.fetch_user(admin_id)
            if admin_id == IDADMIN_GOC:
                msg += f"- `{user.name}` (Admin Gốc)\n"
            else:
                msg += f"- `{user.name}`\n"
        except:
            msg += f"- `{admin_id}` (Không tìm được tên)\n"
    await ctx.send(msg)

# Lưu file
@bot.command()
async def setngonmess(ctx):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền.")
    if not ctx.message.attachments:
        return await ctx.send("Vui lòng đính kèm file.")
    admin_id = str(ctx.author.id)
    file = ctx.message.attachments[0]
    filename = file.filename
    os.makedirs(f"data/{admin_id}", exist_ok=True)
    path = f"data/{admin_id}/{filename}"
    await file.save(path)
    await ctx.send(f"Đã lưu file `{filename}` vào thư mục của bạn.")

# Lệnh listngonmess
@bot.command()
async def listngonmess(ctx):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    
    admin_id = str(ctx.author.id)
    folder = f"data/{admin_id}"
    
    if not os.path.exists(folder):
        return await ctx.send("Bạn chưa lưu file nào. Hãy dùng lệnh `setngonmess` để lưu file.")
    
    files = os.listdir(folder)
    if not files:
        return await ctx.send("Bạn chưa lưu file nào. Hãy dùng lệnh `setngonmess` để lưu file.")
    
    embed = discord.Embed(
        title=f"📁 Danh sách file của {ctx.author.name}",
        description=f"Tổng số file: **{len(files)}**",
        color=discord.Color.green()
    )
    
    for fname in files:
        path = os.path.join(folder, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                preview = content.replace('\n', ' ')[:100]
                line_count = len(content.split('\n'))
                char_count = len(content)
                embed.add_field(
                    name=f"📄 {fname}",
                    value=f"📝 {preview}...\n📊 {line_count} dòng | {char_count} ký tự",
                    inline=False
                )
        except:
            embed.add_field(
                name=f"📄 {fname}",
                value="⚠️ Không đọc được nội dung file",
                inline=False
            )
    
    embed.set_footer(text=f"Dùng {PREFIX}ngonmess <id> <cookie> <tên_file> <delay> để spam")
    await ctx.send(embed=embed)

@bot.command()
async def xemngonmess(ctx, filename: str = None):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền.")
    
    admin_id = str(ctx.author.id)
    folder = f"data/{admin_id}"
    
    if not os.path.exists(folder):
        return await ctx.send("Bạn chưa lưu file nào.")
    
    if filename is None:
        files = os.listdir(folder)
        if not files:
            return await ctx.send("Bạn chưa lưu file nào.")
        msg = f"**Danh sách file của `{ctx.author.name}`:**\n"
        for fname in files:
            path = os.path.join(folder, fname)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    preview = f.read(100).replace('\n', ' ')
                    msg += f"`{fname}`: {preview}...\n"
            except:
                msg += f"`{fname}`: (Không đọc được nội dung)\n"
        await ctx.send(msg)
    else:
        file_path = f"{folder}/{filename}"
        if not os.path.exists(file_path):
            return await ctx.send(f"File `{filename}` không tồn tại.")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if len(content) > 1900:
                chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
                await ctx.send(f"**Nội dung file `{filename}`:**\n```{chunks[0]}```")
                for chunk in chunks[1:]:
                    await ctx.send(f"```{chunk}```")
            else:
                await ctx.send(f"**Nội dung file `{filename}`:**\n```{content}```")
        except Exception as e:
            await ctx.send(f"Lỗi đọc file: {str(e)}")

# Lệnh ngonmess
@bot.command()
async def ngonmess(ctx, id_box: str, cookie: str, filename: str, speed: float):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")

    admin_id = str(ctx.author.id)
    file_path = f"data/{admin_id}/{filename}"

    if not os.path.exists(file_path):
        return await ctx.send(f"File `{filename}` không tồn tại. Dùng `{PREFIX}listngonmess` để xem danh sách.")

    cm = CookieManager(cookie, id_box)
    if not cm.init_params():
        return await ctx.send("Cookie không hợp lệ hoặc không lấy được thông tin.")

    with open(file_path, 'r', encoding='utf-8') as f:
        message_body = f.read().strip()

    task_id = f"ngonmess_{id_box}_{time.time()}"
    cookie_managers[task_id] = cm
    
    async def spam_loop_task():
        while True:
            success = send_message_with_manager(cm, message_body)
            if success:
                print(f"{COLOR_SUCCESS}[+] Đã gửi 1 tin nhắn vào box {id_box}{trang}")
            else:
                print(f"{COLOR_ERROR}[!] Gửi thất bại vào box {id_box}{trang}")
            await asyncio.sleep(speed)

    task = asyncio.create_task(spam_loop_task())
    running_tasks[task_id] = task
    task_info[task_id] = {'admin_id': ctx.author.id, 'start_time': time.time()}
    await ctx.send(f"Đã bắt đầu spam vào box `{id_box}` với file `{filename}` tốc độ `{speed}` giây.")

@bot.command()
async def stopngonmess(ctx, idgroup: str):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    
    tasks_to_stop = [task_id for task_id in running_tasks if task_id.startswith(f"ngonmess_{idgroup}")]
    if not tasks_to_stop:
        return await ctx.send(f"Không có task nào đang chạy cho nhóm `{idgroup}`.")
    
    for task_id in tasks_to_stop:
        if task_info.get(task_id, {}).get('admin_id') == ctx.author.id or ctx.author.id == IDADMIN_GOC:
            try:
                running_tasks[task_id].cancel()
            except:
                pass
            del running_tasks[task_id]
            if task_id in cookie_managers:
                del cookie_managers[task_id]
            if task_id in task_info:
                del task_info[task_id]
            await ctx.send(f"Đã dừng task cho nhóm `{idgroup}`.")
        else:
            await ctx.send(f"Bạn không có quyền dừng task `{task_id}`.")

# Lệnh nhay
@bot.command()
async def nhay(ctx, id_box: str, cookie: str, speed: float):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")

    path = "nhay.txt"
    if not os.path.exists(path):
        return await ctx.send("Không tìm thấy file `nhay.txt` trong thư mục gốc.")

    cm = CookieManager(cookie, id_box)
    if not cm.init_params():
        return await ctx.send("Cookie không hợp lệ hoặc không lấy được thông tin.")

    with open(path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]
    
    if not lines:
        return await ctx.send("File `nhay.txt` rỗng.")

    task_id = f"nhay_{id_box}_{time.time()}"
    cookie_managers[task_id] = cm
    
    async def loop_nhay():
        index = 0
        while True:
            send_message_with_manager(cm, lines[index])
            index = (index + 1) % len(lines)
            await asyncio.sleep(speed)

    task = asyncio.create_task(loop_nhay())
    running_tasks[task_id] = task
    task_info[task_id] = {'admin_id': ctx.author.id, 'start_time': time.time()}
    await ctx.send(f"Đã bắt đầu nhảy tin nhắn vào box `{id_box}` với tốc độ `{speed}` giây.")

@bot.command()
async def stopnhay(ctx, id_box: str):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    
    tasks_to_stop = [task_id for task_id in running_tasks if task_id.startswith(f"nhay_{id_box}")]
    if not tasks_to_stop:
        return await ctx.send(f"Không có task nào đang chạy cho box `{id_box}`.")
    
    for task_id in tasks_to_stop:
        if task_info.get(task_id, {}).get('admin_id') == ctx.author.id or ctx.author.id == IDADMIN_GOC:
            try:
                running_tasks[task_id].cancel()
            except:
                pass
            del running_tasks[task_id]
            if task_id in cookie_managers:
                del cookie_managers[task_id]
            if task_id in task_info:
                del task_info[task_id]
            await ctx.send(f"Đã dừng task nhay cho box `{id_box}`.")
        else:
            await ctx.send(f"Bạn không có quyền dừng task `{task_id}`.")

# Lệnh reo
@bot.command()
async def reo(ctx, id_box: str, cookie: str, delay: float):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    
    file_path = "nhay.txt"
    if not os.path.exists(file_path):
        return await ctx.send("File `nhay.txt` không tồn tại.")

    await ctx.send("Vui lòng nhập ID người cần tag:")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        msg = await bot.wait_for('message', timeout=30.0, check=check)
        tagged_id = msg.content.strip()
        if not tagged_id.isdigit():
            return await ctx.send("ID tag phải là số hợp lệ.")
    except asyncio.TimeoutError:
        return await ctx.send("Hết thời gian chờ nhập ID tag.")

    cm = CookieManager(cookie, id_box)
    if not cm.init_params():
        return await ctx.send("Cookie không hợp lệ hoặc không lấy được thông tin.")

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        return await ctx.send("File `nhay.txt` rỗng.")

    task_id = f"reo_{id_box}_{time.time()}"
    cookie_managers[task_id] = cm
    
    async def spam_reo():
        index = 0
        while True:
            content = f"{lines[index]} @[{tagged_id}:0]"
            send_message_with_manager(cm, content)
            index = (index + 1) % len(lines)
            await asyncio.sleep(delay)

    task = asyncio.create_task(spam_reo())
    running_tasks[task_id] = task
    task_info[task_id] = {'admin_id': ctx.author.id, 'start_time': time.time(), 'tagged_id': tagged_id}
    await ctx.send(f"Đã bắt đầu reo vào box `{id_box}` tag ID `{tagged_id}` tốc độ `{delay}` giây.")

@bot.command()
async def stopreo(ctx, id_box: str):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")
    
    tasks_to_stop = [task_id for task_id in running_tasks if task_id.startswith(f"reo_{id_box}")]
    if not tasks_to_stop:
        return await ctx.send(f"Không có task reo nào đang chạy cho box `{id_box}`.")
    
    for task_id in tasks_to_stop:
        if task_info.get(task_id, {}).get('admin_id') == ctx.author.id or ctx.author.id == IDADMIN_GOC:
            try:
                running_tasks[task_id].cancel()
            except:
                pass
            del running_tasks[task_id]
            if task_id in cookie_managers:
                del cookie_managers[task_id]
            if task_id in task_info:
                del task_info[task_id]
            await ctx.send(f"Đã dừng reo cho box `{id_box}`.")
        else:
            await ctx.send(f"Bạn không có quyền dừng task `{task_id}`.")

# Icon cho codelag
icon_code = "⃟꙰⃟꙰⃟꙰꙰⃟꙰⃟꙰⃟꙰꙰"

@bot.command()
async def codelag(ctx, id_box: str, cookie: str, speed: float):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")

    path = "nhay.txt"
    if not os.path.exists(path):
        return await ctx.send("Không tìm thấy file `nhay.txt`.")

    cm = CookieManager(cookie, id_box)
    if not cm.init_params():
        return await ctx.send("Cookie không hợp lệ hoặc không lấy được thông tin.")

    with open(path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]

    task_id = f"codelag_{id_box}_{time.time()}"
    cookie_managers[task_id] = cm

    async def loop_codelag():
        index = 0
        while True:
            message = f"{lines[index]} {icon_code}"
            send_message_with_manager(cm, message)
            index = (index + 1) % len(lines)
            await asyncio.sleep(speed)

    task = asyncio.create_task(loop_codelag())
    running_tasks[task_id] = task
    task_info[task_id] = {'admin_id': ctx.author.id, 'start_time': time.time()}
    await ctx.send(f"Đã bắt đầu codelag vào box `{id_box}` với tốc độ `{speed}` giây.")

@bot.command()
async def stopcodelag(ctx, id_box: str):
    if ctx.author.id not in admins:
        return await ctx.send("Bạn không có quyền sử dụng lệnh này.")

    tasks_to_stop = [task_id for task_id in running_tasks if task_id.startswith(f"codelag_{id_box}")]
    if not tasks_to_stop:
        return await ctx.send(f"Không có task codelag nào đang chạy cho box `{id_box}`.")

    for task_id in tasks_to_stop:
        if task_info.get(task_id, {}).get('admin_id') == ctx.author.id or ctx.author.id == IDADMIN_GOC:
            try:
                running_tasks[task_id].cancel()
            except:
                pass
            del running_tasks[task_id]
            if task_id in cookie_managers:
                del cookie_managers[task_id]
            if task_id in task_info:
                del task_info[task_id]
            await ctx.send(f"Đã dừng codelag cho box `{id_box}`.")
        else:
            await ctx.send(f"Bạn không có quyền dừng task `{task_id}`.")

# Các lệnh tab
@bot.command()
async def tabngonmess(ctx):
    admin_task_count = {}
    for task_id, info in task_info.items():
        if task_id.startswith("ngonmess_"):
            admin_id = info.get('admin_id')
            if admin_id:
                admin_task_count[admin_id] = admin_task_count.get(admin_id, 0) + 1

    if not admin_task_count:
        return await ctx.send("Hiện không có task ngonmess nào chạy.")

    admin_list = list(admin_task_count.items())
    msg = "**Danh sách admin đang có task:**\n"
    for i, (admin_id, count) in enumerate(admin_list, start=1):
        try:
            user = await bot.fetch_user(admin_id)
            msg += f"{i}. Admin {user.mention} đã tạo {count} task.\n"
        except:
            msg += f"{i}. Admin ID {admin_id} đã tạo {count} task.\n"
    await ctx.send(msg)

@bot.command()
async def tabnhay(ctx):
    admin_task_count = {}
    for task_id, info in task_info.items():
        if task_id.startswith("nhay_"):
            admin_id = info.get('admin_id')
            if admin_id:
                admin_task_count[admin_id] = admin_task_count.get(admin_id, 0) + 1

    if not admin_task_count:
        return await ctx.send("Hiện không có task nhay nào chạy.")

    admin_list = list(admin_task_count.items())
    msg = "**Danh sách admin đang có task:**\n"
    for i, (admin_id, count) in enumerate(admin_list, start=1):
        try:
            user = await bot.fetch_user(admin_id)
            msg += f"{i}. Admin {user.mention} đã tạo {count} task.\n"
        except:
            msg += f"{i}. Admin ID {admin_id} đã tạo {count} task.\n"
    await ctx.send(msg)

@bot.command()
async def tabcodelag(ctx):
    admin_task_count = {}
    for task_id, info in task_info.items():
        if task_id.startswith("codelag_"):
            admin_id = info.get('admin_id')
            if admin_id:
                admin_task_count[admin_id] = admin_task_count.get(admin_id, 0) + 1

    if not admin_task_count:
        return await ctx.send("Hiện không có task codelag nào chạy.")

    admin_list = list(admin_task_count.items())
    msg = "**Danh sách admin đang có task:**\n"
    for i, (admin_id, count) in enumerate(admin_list, start=1):
        try:
            user = await bot.fetch_user(admin_id)
            msg += f"{i}. Admin {user.mention} đã tạo {count} task.\n"
        except:
            msg += f"{i}. Admin ID {admin_id} đã tạo {count} task.\n"
    await ctx.send(msg)

# Menu
@bot.command()
async def menu(ctx):
    embed = discord.Embed(
        title="『 **Menu Bot Facebook** 』",
        description=f"""
Admin: Real Love And Forever Time
Prefix: `{PREFIX}`

**Admin & Quản lý**
🔷 `{PREFIX}addadmin @tag` – Thêm admin
🔷 `{PREFIX}deladmin @tag` – Xoá admin
🔷 `{PREFIX}listadmin` – DS admin

**Quản lý file**
🔷 `{PREFIX}setngonmess [file]` – Lưu file
🔷 `{PREFIX}listngonmess` – DS file đã lưu
🔷 `{PREFIX}xemngonmess <tên_file>` – Xem nội dung file

**Treo Messenger**
🔷 `{PREFIX}ngonmess <id> <cookie> <file> <delay>` – Spam
🔷 `{PREFIX}stopngonmess <id>` – Dừng
🔷 `{PREFIX}tabngonmess` – DS task

**Ré/Nhảy Messenger**
🔷 `{PREFIX}reo <id> <cookie> <delay>` – Réo tag
🔷 `{PREFIX}stopreo <id>` – Dừng réo
🔷 `{PREFIX}nhay <id> <cookie> <delay>` – Nhảy tin
🔷 `{PREFIX}stopnhay <id>` – Dừng nhảy
🔷 `{PREFIX}tabnhay` – DS task nhay

**Codelag Messenger**
🔷 `{PREFIX}codelag <id> <cookie> <delay>` – Code lag
🔷 `{PREFIX}stopcodelag <id>` – Dừng
🔷 `{PREFIX}tabcodelag` – DS task

**Thông tin**
🔷 Admin Gốc: <@{IDADMIN_GOC}>
""",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

# Chạy bot
async def main():
    await bot.wait_until_ready()
    bot.loop.create_task(ram_cleaner_loop())

# Khởi động bot
if __name__ == "__main__":
    bot.loop.create_task(ram_cleaner_loop())
    bot.run(TOKEN)
