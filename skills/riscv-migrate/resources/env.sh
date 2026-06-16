# 自动生成：source 本文件即可加载本技能环境变量
# 用法: source "/home/10356995/xbr_doc/xbr/rag-riscv/skill/riscv-migrate/resources/env.sh"

ENV_D_DIR="/home/10356995/xbr_doc/xbr/rag-riscv/skill/riscv-migrate/resources/env.d"
if [[ -d "${ENV_D_DIR}" ]]; then
  for f in "${ENV_D_DIR}"/*.sh; do
    [[ -f "${f}" ]] || continue
    # shellcheck disable=SC1090
    source "${f}"
  done
fi
