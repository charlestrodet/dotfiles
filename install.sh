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
files='
.tmux.conf
.config/kitty/kitty.conf
.config/kitty/current-theme.conf
.config/fish/config.fish
.config/fish/functions/fish_prompt.fish
'

# Check the source tree before changing the destination.
for relative_path in $files; do
    if [ ! -f "$repo_dir/home/$relative_path" ]; then
        printf 'Missing source: %s\n' "$repo_dir/home/$relative_path" >&2
        exit 1
    fi
done

for relative_path in $files; do
    source_path=$repo_dir/home/$relative_path
    target_path=$HOME/$relative_path

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
done
