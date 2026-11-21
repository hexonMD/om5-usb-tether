#!/bin/sh
cd -P "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
    studio_python=".venv/bin/python"
elif [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    studio_python="$VIRTUAL_ENV/bin/python"
else
    studio_python="python3"
fi
"$studio_python" jewelry_studio.py "$@"
studio_result=$?
if [ "$studio_result" -ne 0 ]; then
    echo "For setup instructions, open README.md." >&2
    if [ "$#" -eq 0 ] && [ -t 0 ]; then
        printf 'Press Return to close. '
        read -r ignored
    fi
fi
exit "$studio_result"
