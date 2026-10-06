#!/bin/bash

trap "" SIGPIPE
# 安装 napcat
if [ ! -f "napcat/napcat.mjs" ]; then
    unzip -q NapCat.Shell.zip -d ./NapCat.Shell
    cp -rf NapCat.Shell/* napcat/
    rm -rf ./NapCat.Shell
fi
if [ ! -f "napcat/config/napcat.json" ]; then
    unzip -q NapCat.Shell.zip -d ./NapCat.Shell
    cp -rf NapCat.Shell/config/* napcat/config/
    rm -rf ./NapCat.Shell
fi

# 配置 WebUI Token
CONFIG_PATH=/app/napcat/config/webui.json

if [ ! -f "${CONFIG_PATH}" ] && [ -n "${WEBUI_TOKEN}" ]; then
    echo "正在配置 WebUI Token..."
    cat > "${CONFIG_PATH}" << EOF
{
    "host": "0.0.0.0",
    "prefix": "${WEBUI_PREFIX}",
    "port": 6099,
    "token": "${WEBUI_TOKEN}",
    "loginRate": 3
}
EOF
fi

# 删除字符串两端的引号
remove_quotes() {
    local str="$1"
    local first_char="${str:0:1}"
    local last_char="${str: -1}"

    if [[ ($first_char == '"' && $last_char == '"') || ($first_char == "'" && $last_char == "'") ]]; then
        # 两端都是双引号
        if [[ $first_char == '"' ]]; then
            str="${str:1:-1}"
        # 两端都是单引号
        else
            str="${str:1:-1}"
        fi
    fi
    echo "$str"
}

if [ -n "${MODE}" ]; then
    cp /app/templates/$MODE.json /app/napcat/config/onebot11.json
fi

rm -rf "/tmp/.X1-lock"

# 删除容器标识
rm -f "/.dockerenv"
rm -f "/.dockerinit"
rm -f "/run/.containerenv"
rm -f "/run/systemd/container"
rm -f "/dev/.dockerenv"

# Name规范
HOSTNAME_VAL="$(hostname)"
if [[ "${HOSTNAME_VAL}" == *docker* || "${HOSTNAME_VAL}" == *container* || "${HOSTNAME_VAL}" == *lxc* ]] \
   || [[ "${HOSTNAME_VAL}" =~ ^[a-f0-9]{12,}$ ]]; then
    hostname "localhost"
    echo "localhost" > /etc/hostname
fi

# cgroup处理
mkdir -p /tmp/fake_cgroup
FAKE_CGROUP_DIR="/tmp/fake_cgroup"
# /proc/self/cgroup 
# /proc/1/cgroup
for cfile in /proc/self/cgroup /proc/1/cgroup; do
    if [ -f "$cfile" ]; then
        FAKE_CG="$FAKE_CGROUP_DIR/cgroup_$(basename $(dirname $cfile))_$(basename $cfile)"
        cat "$cfile" | sed \
            -e 's|/docker/|/system.slice/|g' \
            -e 's|/docker-|/system.slice/|g' \
            -e 's|/lxc/|/system.slice/|g' \
            -e 's|/podmaster|/system.slice/|g' \
            -e 's|/podman/|/system.slice/|g' \
            -e 's|/kubepods/|/system.slice/|g' \
            -e 's|/containerd/|/system.slice/|g' \
            -e 's|/buildkit/|/system.slice/|g' \
            > "$FAKE_CG"
        mount --bind "$FAKE_CG" "$cfile" 2>/dev/null || true
    fi
done

# DMI
for dmi_file in /sys/class/dmi/id/product_name /sys/class/dmi/id/product_uuid /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/board_vendor /sys/class/dmi/id/board_name; do
    if [ -f "$dmi_file" ]; then
        FAKE_DMI="/tmp/fake_cgroup/$(echo $dmi_file | tr '/' '_')"
        case "$(basename $dmi_file)" in
            product_name)  echo "Standard PC" > "$FAKE_DMI" ;;
            product_uuid)  echo "00000000-0000-0000-0000-000000000000" > "$FAKE_DMI" ;;
            sys_vendor|board_vendor) echo "Intel Corporation" > "$FAKE_DMI" ;;
            board_name)    echo "Default string" > "$FAKE_DMI" ;;
        esac
        mount --bind "$FAKE_DMI" "$dmi_file" 2>/dev/null || true
    fi
done

# Docker Socket
if [ -S /var/run/docker.sock ]; then
    mv /var/run/docker.sock /var/run/.docker.sock.hidden 2>/dev/null || true
fi

# Mount Info
for mfile in /proc/self/mountinfo /proc/1/mountinfo; do
    if [ -f "$mfile" ]; then
        FAKE_MOUNT="$FAKE_CGROUP_DIR/mountinfo_$(basename $(dirname $mfile))_$(basename $mfile)"
        cat "$mfile" | sed \
            -e 's|overlay / |ext4 / |g' \
            -e 's|overlay /|ext4 /|g' \
            -e '/docker/d' \
            -e '/containerd/d' \
            -e '/\.dockerenv/d' \
            -e '/cgroup2.*docker/d' \
            > "$FAKE_MOUNT"
        mount --bind "$FAKE_MOUNT" "$mfile" 2>/dev/null || true
    fi
done

# /proc/1/cmdline
if [ -f /proc/1/cmdline ]; then
    FAKE_CMDLINE="$FAKE_CGROUP_DIR/cmdline_1"
    printf '/sbin/init\0' > "$FAKE_CMDLINE"
    mount --bind "$FAKE_CMDLINE" /proc/1/cmdline 2>/dev/null || true
fi

# /proc/1/sched
if [ -f /proc/1/sched ]; then
    FAKE_SCHED="$FAKE_CGROUP_DIR/sched_1"
    head -1 /proc/1/sched | sed 's|([^)]*)|(1, #threads=1)|' > "$FAKE_SCHED"
    tail -n +2 /proc/1/sched >> "$FAKE_SCHED"
    sed -i 's/^[a-zA-Z0-9_-]*\s/systemd /' "$FAKE_SCHED"
    mount --bind "$FAKE_SCHED" /proc/1/sched 2>/dev/null || true
fi

# /proc/1/environ
if [ -f /proc/1/environ ]; then
    FAKE_ENVIRON="$FAKE_CGROUP_DIR/environ_1"
    cat /proc/1/environ | tr '\0' '\n' | \
        grep -v -i 'container=docker\|container=lxc\|container=podman\|container=containerd' | \
        tr '\n' '\0' > "$FAKE_ENVIRON"
    mount --bind "$FAKE_ENVIRON" /proc/1/environ 2>/dev/null || true
fi

# /proc/1/status & /proc/self/status (NSpid)
for sfile in /proc/1/status /proc/self/status; do
    if [ -f "$sfile" ]; then
        FAKE_STATUS="$FAKE_CGROUP_DIR/status_$(basename $(dirname $sfile))_$(basename $sfile)"
        cat "$sfile" | sed 's/^NSpid:.*/NSpid:\t1/' > "$FAKE_STATUS"
        mount --bind "$FAKE_STATUS" "$sfile" 2>/dev/null || true
    fi
done

# /proc/1/stat
if [ -f /proc/1/stat ]; then
    FAKE_STAT="$FAKE_CGROUP_DIR/stat_1"
    cat /proc/1/stat | sed 's/^\([0-9]*\) ([-a-zA-Z0-9_]*)/\1 (systemd)/' > "$FAKE_STAT"
    mount --bind "$FAKE_STAT" /proc/1/stat 2>/dev/null || true
fi

# /etc/hosts
if [ -f /etc/hosts ]; then
    FAKE_HOSTS="$FAKE_CGROUP_DIR/hosts"
    cat /etc/hosts | grep -v "$(hostname)" > "$FAKE_HOSTS" 2>/dev/null
    grep -q '^127\.0\.0\.1.*localhost' "$FAKE_HOSTS" || echo "127.0.0.1\tlocalhost" >> "$FAKE_HOSTS"
    grep -q '^::1.*localhost' "$FAKE_HOSTS" || echo "::1\tlocalhost ip6-localhost ip6-loopback" >> "$FAKE_HOSTS"
    mount --bind "$FAKE_HOSTS" /etc/hosts 2>/dev/null || true
fi

# /sys/hypervisor/type
if [ -f /sys/hypervisor/type ]; then
    FAKE_HYPER="$FAKE_CGROUP_DIR/hypervisor_type"
    echo "" > "$FAKE_HYPER"
    mount --bind "$FAKE_HYPER" /sys/hypervisor/type 2>/dev/null || true
fi

# /proc/self/mounts
if [ -f /proc/self/mounts ]; then
    FAKE_MOUNTS="$FAKE_CGROUP_DIR/mounts_self"
    cat /proc/self/mounts | sed \
        -e 's|overlay / |ext4 / |g' \
        -e 's|overlay /|ext4 /|g' \
        -e '/docker/d' \
        -e '/containerd/d' \
        > "$FAKE_MOUNTS"
    mount --bind "$FAKE_MOUNTS" /proc/self/mounts 2>/dev/null || true
fi

# /proc/self/cpuset
if [ -f /proc/self/cpuset ]; then
    FAKE_CPUSET="$FAKE_CGROUP_DIR/cpuset_self"
    cat /proc/self/cpuset | sed \
        -e 's|/docker/|/|g' \
        -e 's|/docker-[^/]*/|/|g' \
        -e 's|/kubepods/|/|g' \
        -e 's|/containerd/|/|g' \
        > "$FAKE_CPUSET"
    mount --bind "$FAKE_CPUSET" /proc/self/cpuset 2>/dev/null || true
fi

: ${NAPCAT_GID:=0}
: ${NAPCAT_UID:=0}
usermod -o -u ${NAPCAT_UID} napcat
groupmod -o -g ${NAPCAT_GID} napcat
usermod -g ${NAPCAT_GID} napcat
chown -R ${NAPCAT_UID}:${NAPCAT_GID} /app

# 关闭 core dump。QQ 会预留上 TB 的虚拟地址空间，崩溃时 core 能写出几百 GB，
# Docker Desktop (WSL) 下会直接写满宿主机系统盘（NapCatQQ #2060）。
# WSL 这类管道形式的 core_pattern 不受 ulimit 限制，所以同时把 coredump_filter 清零，
# 崩溃时只留寄存器等基本信息。需要完整 core 调试时设置 NAPCAT_ENABLE_COREDUMP=1。
if [ "${NAPCAT_ENABLE_COREDUMP}" != "1" ]; then
    ulimit -c 0 2>/dev/null || true
    echo 0 > /proc/self/coredump_filter 2>/dev/null || true
fi

# ============================================================
# 以下为 SnowLuma 分支（替换原 NapCat 启动段）
# 伪装层 / Xvfb / usermod / coredump 关停 均沿用上方 NapCat 逻辑
# ============================================================

# --- 关键：还原 QQ 官方入口 ---
# napcat-docker 镜像把 resources/app/package.json 的 main 改成了 ./loadNapCat.js，
# 使 QQ 一启动就在自身 Node 上下文里加载 NapCat。若不还原，SnowLuma 实验里
# 跑的其实是 NapCat + SnowLuma 双 hook，结论无效。
QQ_PKG=/opt/QQ/resources/app/package.json
if grep -qF 'main": "./loadNapCat.js' "$QQ_PKG" 2>/dev/null; then
    cp "$QQ_PKG" "$QQ_PKG.napcat-bak" 2>/dev/null || true
    sed -i 's#"main": "./loadNapCat.js"#"main": "./application.asar/app_launcher/index.js"#' "$QQ_PKG"
    echo "[snowluma-entrypoint] 已还原 QQ 官方入口 -> ./application.asar/app_launcher/index.js"
fi
grep -o '"main": "[^"]*"' "$QQ_PKG" || true

gosu napcat Xvfb :1 -screen 0 1080x760x16 +extension GLX +render > /dev/null 2>&1 &
sleep 2

export FFMPEG_PATH=/usr/bin/ffmpeg
export DISPLAY=:1

# --- SnowLuma 运行参数 ---
export SNOWLUMA_ACCEPT_EULA=1
export SNOWLUMA_ACCEPT_PRIVACY=1
export SNOWLUMA_WEBUI_HOST=0.0.0.0
export SNOWLUMA_WEBUI_PORT=5099
export SNOWLUMA_HOOK_AUTOLOAD=1
export SNOWLUMA_UPDATE_CHECK=0
export SNOWLUMA_LOG_DIR=/app/snowluma/logs

# hook 运行时目录：SnowLuma 运行时与注入进 QQ 的 .so 必须看到同一路径
export XDG_RUNTIME_DIR=/tmp/snowluma-runtime
mkdir -p "$XDG_RUNTIME_DIR" "$SNOWLUMA_LOG_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# --- 二维码刷新工具（best-effort，失败不影响启动）---
# SnowLuma 没有二维码 API，登录二维码只能从 Xvfb 画面截图拿到；二维码过期后需要
# 点击登录窗口里的「刷新」按钮，因此准备 xdotool。后台安装，不阻塞启动。
# 注意：镜像内已装的 linuxqq 依赖 xdg-utils，缺失会让 apt 拒绝安装新包，
# 所以必须把 xdg-utils / libxdo3 一起显式列出。
if ! command -v xdotool >/dev/null 2>&1; then
    (
        apt-get update -qq >/dev/null 2>&1
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
            xdg-utils libxdo3 xdotool >/dev/null 2>&1
    ) &
fi

# --- 启动 QQ 客户端（SnowLuma 随后 ptrace 注入自身）---
mkdir -p /tmp/qqlog
if [ -n "${ACCOUNT}" ]; then
    gosu napcat /opt/QQ/qq --no-sandbox -q $ACCOUNT > /tmp/qqlog/qq.log 2>&1 &
else
    gosu napcat /opt/QQ/qq --no-sandbox > /tmp/qqlog/qq.log 2>&1 &
fi

echo "[snowluma-entrypoint] QQ 已启动 (ACCOUNT=${ACCOUNT:-<未设置>})，准备启动 SnowLuma"
cd /app/snowluma
exec ./launcher.sh
