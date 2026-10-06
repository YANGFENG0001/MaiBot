#!/bin/sh
set -eu

# 把镜像内置的适配器模板复制进插件目录，仅当目标不存在时执行。
#
# 内置哪些适配器由 Dockerfile 决定（当前是 snowluma-adapter）；这里保持通用，
# 不再把某个协议端写死在脚本里，避免换协议端时要同时改两处。
#
# 注意：本脚本每次容器启动都会运行。若某个内置模板对应的插件已被用户在 WebUI 里
# 卸载，这里会把它重新装回去 —— 需要彻底移除时应改 Dockerfile 去掉对应模板。
TEMPLATE_ROOT="/MaiMBot/plugin-templates"
PLUGIN_ROOT="/MaiMBot/plugins"

mkdir -p "$PLUGIN_ROOT"

if [ -d "$TEMPLATE_ROOT" ]; then
    for template in "$TEMPLATE_ROOT"/*; do
        [ -d "$template" ] || continue
        name=$(basename "$template")
        if [ -e "$PLUGIN_ROOT/$name" ]; then
            echo "[plugin-template] 已存在，跳过：$name"
        else
            cp -a "$template" "$PLUGIN_ROOT/$name"
            echo "[plugin-template] 已安装内置适配器：$name"
        fi
    done
fi

/MaiMBot/.venv/bin/python /MaiMBot/src/plugin_runtime/docker_layout_migration.py

exec /MaiMBot/.venv/bin/python bot.py "$@"
