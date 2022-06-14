#!/usr/bin/env python3
from threading  import Thread
import logging
import os
import errno
import subprocess
import signal
import shlex
import psutil
import json
import time
import asyncio
import sys
import re
from queue import Queue, Empty

from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

HOME = os.path.split(os.path.realpath(__file__))[0]
config_f = os.path.join(HOME, "bot.cfg")
config = {}

if not os.path.exists(config_f):
    logger.warning(f"no config file:{config_f}")
    sys.exit(-1)

with open(config_f, "r") as f:
    try:
        config = json.load(f)
    except Exception as e:
        logger.debug(e)
        logger.warning("load config faild.")
        sys.exit(-2)

WORK_DIR="/root/Downloads"
if config.get("work_dir"):
    WORK_DIR = config.get("work_dir")

if not os.path.exists(WORK_DIR):
    os.makedirs(WORK_DIR)
os.chdir(WORK_DIR)

try:
    tg_token = config["token"]
    chat_id = config["chat_id"]
except KeyError:
    logger.error("配置不完整")
    sys.exit(-2)

is_public = False
if config.get("public"):
    is_public = True
    tg_token = os.environ["TOKEN"]
    os.environ["TOKEN"] = ""

def wait_child(signum, frame):
    logging.info('receive SIGCHLD')
    try:
        while True:
            # -1 表示任意子进程
            # os.WNOHANG 表示如果没有可用的需要 wait 退出状态的子进程，立即返回不阻塞
            cpid, status = os.waitpid(-1, os.WNOHANG)
            if cpid == 0:
                logging.info('no child process was immediately available')
                break
            exitcode = status >> 8
            logging.info('child process %s exit with exitcode %s', cpid, exitcode)
    except OSError as e:
        if e.errno == errno.ECHILD:
            logging.warning('current process has no existing unwaited-for child processes.')
        else:
            raise
    logging.info('handle SIGCHLD end')

signal.signal(signal.SIGCHLD, wait_child)

def permission_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user['id'] not in chat_id and not is_public:
            text = f"Invalid user {user['id']}."
            logger.info(text)
            await update.message.reply_text(text)
            return
        await func(update, context)
    return wrapper

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Hello!{update.effective_user['id']}")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Help!')


# Bash
def kill_pid_tree(pid):
    parent = psutil.Process(pid)
    for child in parent.children(recursive=True): # or parent.children() for recursive=False
        child.send_signal(signal.SIGINT)
        child.kill()

    parent.send_signal(signal.SIGINT)
    parent.kill()

def enqueue_output(out, queue):
    while True:
        try:
            line = out.readline()
            if len(line.strip()) > 0:
                print(line)
                queue.put(line)
            if not line:
                break
        except Exception as e:
            logging.info(f"{out} close")
            break

async def cmd_reply(update: Update, queue: Queue):
    last = time.time()
    reply = []
    cache = []

    quiet = False
    while True:
        await asyncio.sleep(1)
        try:
            line = queue.get_nowait()
        except Empty:
            ...
        else:
            if line is None:
                if len(reply) > 0:
                    await update.message.reply_text("".join(reply), disable_web_page_preview = True)
                break
            # 静默
            if line == "QUIET":
                quiet = True
            # 非静默
            elif line == "VERBOSE":
                quiet = False
            elif line == "POLL" and quiet:
                while len(cache) > 0:
                    text = "".join(cache[0:30])
                    if len(text) > 0:
                        await update.message.reply_text(text, disable_web_page_preview = True)
                    cache = cache[30:]
                cache = []
            else:
                if quiet:
                    cache.append(line)
                    if len(cache) > 110:
                        cache = cache[-100:]
                else:
                    reply.append(line)

        now = time.time()
        if now - last >= 1 or len(reply) >= 10:
            text = "".join(reply)
            if len(text) > 0:
                await update.message.reply_text(text, disable_web_page_preview = True)
            reply = []
            last = time.time()

@permission_required
async def bash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("sh") and context.user_data.get("sh").poll() is None:
        await update.message.reply_text(f"Current shell is ok! pid:{context.user_data['sh'].pid}", disable_web_page_preview = True)
        return

    sh = subprocess.Popen(["bash"],
        stdout = subprocess.PIPE,
        stdin = subprocess.PIPE,
        stderr = subprocess.PIPE,
        universal_newlines=True,
        bufsize = 0)
    context.user_data["sh"] = sh

    q = Queue()
    context.user_data["queue"] = q

    t1 = Thread(target=enqueue_output, args=(sh.stdout,q))
    t1.daemon = True
    t1.start()
    context.user_data["output"] = t1

    t2 = Thread(target=enqueue_output, args=(sh.stderr,q))
    t2.daemon = True
    t2.start()
    context.user_data["err"] = t2

    t3 = asyncio.create_task(cmd_reply(update, q))
    context.user_data["cmd_reply"] = t3

    reply = f"Hi! Start bash {sh.pid}"
    print(reply)
    await update.message.reply_text(reply, disable_web_page_preview = True)

@permission_required
async def bash_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["queue"].put("QUIET")

@permission_required
async def bash_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["queue"].put("VERBOSE")

@permission_required
async def bash_polling(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["queue"].put("POLL")

@permission_required
async def bash_sigint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("sh") is None:
        await update.message.reply_text("no context shell")
        return
    sh = context.user_data.get("sh")
    parent = psutil.Process(sh.pid)
    for child in parent.children(recursive=True): # or parent.children() for recursive=False
        child.send_signal(signal.SIGINT)

@permission_required
async def bash_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("sh") is None:
        await update.message.reply_text("no context shell")
        return
    sh = context.user_data.get("sh")

    kill_pid_tree(sh.pid)
    sh.wait()

    sh.stdout.close()
    sh.stderr.close()
    context.user_data["output"].join()
    context.user_data["err"].join()

    context.user_data["queue"].put(None)

    await context.user_data["cmd_reply"]

    del context.user_data["sh"]
    del context.user_data["queue"]
    del context.user_data["output"]
    del context.user_data["err"]
    del context.user_data["cmd_reply"]
    

    await update.message.reply_text("Kill {}!".format(sh.pid))

# Task
async def handle_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE, proc) -> None:
    print(f"args:{proc.args} pid:{proc.pid} return:{proc.returncode} poll:{proc.poll()}\n")
    if proc.returncode != 0:
        text = f"服务器抽风了 pid:{proc.pid} return:{proc.returncode}\n"

        stderr_line = proc.stderr.readlines()[-20:]
        if len(stderr_line):
            text += "\nstderr:\n" + "".join(stderr_line)

        await update.message.reply_text(text, disable_web_page_preview = True)
        return

    try:
        result = json.load(proc.stdout)
        formats = result.get("formats")
        for f in formats:
            try:
                s = "{:.1f} MB".format(f['filesize']/(1024.0*1024))
            except Exception:
                s = f['filesize']

            l = f"[{f['format_note']} asr:{f['asr']} size:{s}]({f['url']})\n"
            await update.message.reply_markdown(l)
    except json.JSONDecodeError as e:
        print(e)
        await update.message.reply_text(f"解析结果出错")
    except Exception as e:
        await update.message.reply_text(f"未知结果出错")
        print(e)

async def handle_youtube_download(update: Update, context: ContextTypes.DEFAULT_TYPE, proc) -> None:
    if proc.returncode != 0:
        text = f"服务器抽风了 pid:{proc.pid} return:{proc.returncode}\n"

        stderr_line = proc.stderr.readlines()[-20:]
        if len(stderr_line):
            text += "\nstderr:\n" + "".join(stderr_line)

        await update.message.reply_text(text, disable_web_page_preview = True)
        return
    text = f"args:{proc.args} pid:{proc.pid} return:{proc.returncode} poll:{proc.poll()}\n下载成功"

    await update.message.reply_text(text, disable_web_page_preview = True)

async def task_polling(update: Update, context: ContextTypes.DEFAULT_TYPE, queue:asyncio.Queue) -> None:
    while True:
        await asyncio.sleep(5)
        try:
            l = context.user_data['task']['list']
            remove = []
            for idx,task in  enumerate(l):
                task_type = task['type']
                task_proc = task["proc"]
                if task_proc.poll() is not None:
                    # terminated
                    remove.append(task)
                elif task['timeout'] is not None:
                    t = time.time() - task["start_ts"]
                    if t > task['timeout']:
                        task_proc.kill()
                        remove.append(task)


            for idx,task in enumerate(remove):
                l.remove(task)

                task_type = task['type']
                proc = task['proc']
                proc.wait()

                if task_type == "youtube":
                    await handle_youtube(update, context, task['proc'])
                    continue

                if task_type == "youtube_download":
                    await handle_youtube_download(update, context, task['proc'])
                    continue

                text = "Task done:\n"
                text += f"[{idx}] args:{proc.args} pid:{proc.pid} return:{proc.returncode} poll:{proc.poll()}\n"

                stdout_line = proc.stdout.readlines()[-20:]
                if len(stdout_line):
                    text += "\nstdout:\n" + "".join(stdout_line)

                stderr_line = proc.stderr.readlines()[-20:]
                if len(stderr_line):
                    text += "\nstderr:\n" + "".join(stderr_line)

                print(text)
                await update.message.reply_text(text, disable_web_page_preview = True)

        except Exception as e:
            logging.warning(f"task_polling error:{e}")

@permission_required
async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''创建队列'''
    if update.edited_message:
        await update.edited_message.reply_text(f"不支持修改")
        return
    if not update.message:
        print("not message")
        return

    if context.user_data.get("task") is not None:
        await update.message.reply_text(f"已存在任务队列")
        return

    queue = asyncio.Queue(16)

    context.user_data['task'] = {
        "list": [],
        "polling_queue": queue,
    }
    context.user_data['task']['polling'] = asyncio.create_task(task_polling(update, context, queue))

    await update.message.reply_text(f"任务队列创建成功")

@permission_required
async def task_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.edited_message:
        await update.edited_message.reply_text(f"不支持修改")
        return
    if not update.message:
        print("not message")
        return

    if context.user_data.get("task"):
        l = context.user_data['task']['list']
        text = "Tasks:\n"
        for idx,task in  enumerate(l):
            proc = task['proc']
            text += f"[{idx}] args:{proc.args} pid:{proc.pid} return:{proc.returncode} poll:{proc.poll()}\n"

        await update.message.reply_text(text, disable_web_page_preview = True)

@permission_required
async def task_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.edited_message:
        await update.edited_message.reply_text(f"不支持修改")
        return
    if not update.message:
        print("not message")
        return

    if context.user_data.get("task") is None:
        await update.message.reply_text("no task context.")
        return
    l = context.user_data['task']['list']

    if len(context.args) != 2:
        await update.message.reply_text("格式不正确, /task_signal pid signal")
        return
    try:
        pid = int(context.args[0])
        sig = int(context.args[1])
    except Exception:
        await update.message.reply_text("格式不正确, /task_signal pid signal")
    else:
        need_signal = [t for t in l if t['proc'].pid == pid]
        if len(need_signal) > 0:
            need_signal[0]['proc'].send_signal(sig)
            await update.message.reply_text(f"send signal {sig} to {pid}.")
        else:
            await update.message.reply_text(f"pid:{pid} not in tasks.")

@permission_required
async def task_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.edited_message:
        await update.edited_message.reply_text(f"不支持修改")
        return
    if not update.message:
        print("not message")
        return

    pid = []
    for i in context.args:
        try:
            pid.append(int(i))
        except Exception:
            ...
    if context.user_data.get("task") is None:
        await update.message.reply_text("no task context.")
        return

    l = context.user_data['task']['list']

    need_kill = [t['proc'] for t in l if t['proc'].pid in pid]
    k = [t.kill() for t in need_kill]
    p = [t.pid for t in need_kill]
    if len(need_kill) > 0:
        await update.message.reply_text(f"Kill:{p}")
    else:
        await update.message.reply_text("pid 无效")

# they are not command!!!
async def do_cmd_by_subprocess(update: Update, context: ContextTypes.DEFAULT_TYPE, cmd = None, task_type = "cmd", timeout = None) -> None:
    if context.user_data.get("task") is None:
        await update.message.reply_text(f"请先执行 /task")
        return

    l = context.user_data['task']['list']
    if len(l) > 16:
        await update.message.reply_text(f"任务队列已满")
        return

    if cmd is None:
        cmd = update.message.text

    os.chdir(WORK_DIR)
    try:
        proc = subprocess.Popen(shlex.split(cmd),
        stdout = subprocess.PIPE,
        stdin = None,
        stderr = subprocess.PIPE,
        universal_newlines=True,
        bufsize = 0)
    except Exception as e:
        print(f"do_cmd_by_subprocess:{cmd}\n{e}")
        await update.message.reply_text(f"{e}", disable_web_page_preview = True)
    else:
        l.append({
            "type": task_type,
            "proc": proc,
            "start_ts": time.time(),
            "timeout": timeout
        })

        await update.message.reply_text(f"任务已提交 args:{proc.args} pid:{proc.pid}", disable_web_page_preview = True)

async def youtube(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    cmd = f"youtube-dl -j \"{text}\""
    await do_cmd_by_subprocess(update, context, cmd, task_type="youtube", timeout = 30)

async def youtube_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    cmd = f"youtube-dl \"{text}\""
    await do_cmd_by_subprocess(update, context, cmd, task_type="youtube_download")

@permission_required
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(update.message.text)
    print(context.user_data)

    text = update.message.text
    if text.startswith("https://"):
        await youtube(update, context)
        return
    
    m = re.match(r"([dD]|(D|d)ownload)\s*(https://\S+)", text)
    if m:
        update.message.text = m.group(3)
        await youtube_download(update, context)
        return

    if context.user_data.get("sh") is None:
        # no context shell
        # await update.message.reply_text("no context shell")
        await do_cmd_by_subprocess(update, context)
        return

    sh = context.user_data.get("sh")
    if sh.poll() is not None:
        await update.message.reply_text("shell died")
        del context.user_data["sh"]
        return

    try:
        print("try:{}\n".format(text))
        sh.stdin.write("{}\n".format(text))
    except Exception as e:
        await update.message.reply_text(e, disable_web_page_preview = True)

def error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)

if __name__ == '__main__':
    application = Application.builder().token(tg_token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))

    application.add_handler(CommandHandler("bash", bash))
    application.add_handler(CommandHandler("bash_quiet", bash_quiet))
    application.add_handler(CommandHandler("bash_verbose", bash_verbose))
    application.add_handler(CommandHandler("bash_polling", bash_polling))
    application.add_handler(CommandHandler("bash_sigint", bash_sigint))
    application.add_handler(CommandHandler("bash_stop", bash_stop))

    application.add_handler(CommandHandler("task", task))
    application.add_handler(CommandHandler("task_list", task_list))
    application.add_handler(CommandHandler("task_signal", task_signal))
    application.add_handler(CommandHandler("task_kill", task_kill))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    application.add_error_handler(error)

    application.run_polling()
