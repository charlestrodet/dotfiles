#!/bin/sh
set -eu

usage() {
    printf 'Usage: %s [--dry-run]\n' "$0"
}

case "${1-}" in
    '') dry_run=0 ;;
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
    usage >&2
    exit 2
fi

case "${HOME-}" in
    /*) ;;
    *) printf 'HOME must be an absolute path.\n' >&2; exit 1 ;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
home_dir=$repo_dir/home
if ! command -v git >/dev/null 2>&1 || [ ! -d "$home_dir" ]; then
    printf 'A Git checkout with a home/ directory is required.\n' >&2
    exit 1
fi
file_list=$(mktemp)
trap 'rm -f "$file_list"' 0
git -C "$repo_dir" -c core.quotePath=false ls-files --cached --others --exclude-standard -- home > "$file_list"
if [ ! -s "$file_list" ]; then
    printf 'No dotfiles found under %s\n' "$home_dir" >&2
    exit 1
fi
while IFS= read -r relative_path; do
    if [ ! -f "$repo_dir/$relative_path" ]; then
        printf 'Missing source: %s\n' "$repo_dir/$relative_path" >&2
        exit 1
    fi
done < "$file_list"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required for Kitty project restoration.\n' >&2
    exit 1
fi
if ! command -v fish >/dev/null 2>&1; then
    printf 'fish is required for this Kitty + tmux configuration.\n' >&2
    exit 1
fi
if [ ! -x /usr/bin/fish ]; then
    printf 'Expected fish at /usr/bin/fish for tmux.\n' >&2
    exit 1
fi

while IFS= read -r relative_path; do
    source_path=$repo_dir/$relative_path
    target_path=$HOME/${relative_path#home/}

    if [ -L "$target_path" ] && [ "$(readlink "$target_path")" = "$source_path" ]; then
        printf 'Already linked: %s\n' "$target_path"
        continue
    fi
    if [ -d "$target_path" ] && [ ! -L "$target_path" ]; then
        printf 'Expected a file, found a directory: %s\n' "$target_path" >&2
        exit 1
    fi

    if [ -e "$target_path" ] || [ -L "$target_path" ]; then
        backup_base=$target_path.backup-$(date +%Y%m%d-%H%M%S)
        backup_path=$backup_base
        backup_number=1
        while [ -e "$backup_path" ] || [ -L "$backup_path" ]; do
            backup_path=$backup_base.$backup_number
            backup_number=$((backup_number + 1))
        done
        printf 'Backup: %s -> %s\n' "$target_path" "$backup_path"
        if [ "$dry_run" -eq 0 ]; then
            mv -- "$target_path" "$backup_path"
        fi
    fi

    printf 'Link: %s -> %s\n' "$target_path" "$source_path"
    if [ "$dry_run" -eq 0 ]; then
        mkdir -p -- "$(dirname -- "$target_path")"
        ln -s -- "$source_path" "$target_path"
    fi
done < "$file_list"

printf 'Initialize local Kitty project state: %s\n' "$HOME/.local/state/kitty-tmux"
if [ "$dry_run" -eq 0 ]; then
    python3 "$repo_dir/home/.config/kitty/project_state.py" init
fi
