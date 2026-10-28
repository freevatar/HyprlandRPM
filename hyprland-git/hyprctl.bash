# Bash completion for hyprctl. Supplement upstream help where it differs from
# the implementation; check-hyprctl-completion.py validates this during RPM builds.
_hyprctl_help_words()
{
    local executable=$1 section=$2
    shift 2

    # hyprctl returns 1 for --help, including when no compositor is running.
    { LC_ALL=C command "$executable" "$@" --help 2>/dev/null || :; } |
        LC_ALL=C awk -v section="$section" '
            /^[^[:space:]]/ { active = ($0 == section ":"); next }
            active && /^    [[:alnum:]_-]+([[:space:]]|$)/ {
                # These remain in 0.57 help but have no built-in IPC handler.
                if (section == "commands" && $1 ~ /^(keyword|layouts|setprop)$/)
                    next
                print $1
                if (section == "flags" && $2 ~ /^\(-[[:alnum:]-]+\)$/) {
                    gsub(/[()]/, "", $2)
                    print $2
                }
            }
        '
}

_hyprctl()
{
    COMPREPLY=()
    local cur=${COMP_WORDS[COMP_CWORD]} executable=${COMP_WORDS[0]}
    local options=1 follow=0 word candidate candidates= i
    local -a args=()
    local -A seen=()

    # Flags may appear before or after the command. Instance values are not
    # positional arguments; batch strings and Lua expressions are left alone.
    for ((i = 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if ((options)); then
            case $word in
                --) options=0; continue ;;
                --batch|--help) return 0 ;;
                -i|--instance)
                    ((i + 1 < COMP_CWORD)) || return 0
                    ((i += 1))
                    continue
                    ;;
                -f|--follow) follow=1; continue ;;
                -j|-r|-a|-c|--config|-q|--quiet) continue ;;
            esac
        fi
        args+=("$word")
    done

    if ((options)) && [[ $cur == -* ]]; then
        candidates=$(_hyprctl_help_words "$executable" flags)
        candidates+=$'\n-a\n-c\n--config\n--help'
        if [[ ${args[0]-} == rollinglog ]]; then
            candidates+=$'\n-f\n--follow'
        elif [[ ${args[0]-} == notify && ${#args[@]} == 1 ]]; then
            candidates+=$'\n-1'
        fi
    elif ((${#args[@]} == 0)); then
        if ((follow)); then
            candidates=rollinglog
        else
            candidates=$(_hyprctl_help_words "$executable" commands)
            [[ -n $candidates ]] || return 0
            candidates+=$'\ndeprecated-config\ndescriptions\nlocked\nreloadshaders\nsubmap'
        fi
    elif ((${#args[@]} == 1)); then
        case ${args[0]} in
            hyprpaper|hyprsunset|plugin)
                candidates=$(_hyprctl_help_words "$executable" requests "${args[0]}")
                if [[ ${args[0]} == hyprpaper ]]; then
                    candidates+=$'\nlistactive'
                fi
                ;;
            monitors) candidates=all ;;
            reload) candidates=full-reset ;;
            output) candidates=$'create\nadd\nremove\ndestroy' ;;
            switchxkblayout) candidates=$'main\nactive\ncurrent\nall' ;;
            notify) candidates=$'-1\n0\n1\n2\n3\n4\n5\n6' ;;
        esac
    elif ((${#args[@]} == 2)); then
        case ${args[0]}:${args[1]} in
            output:create|output:add) candidates=$'auto\nheadless\nwayland' ;;
            switchxkblayout:*) candidates=$'next\nprev' ;;
            plugin:load|plugin:unload)
                compopt -o filenames 2>/dev/null || :
                mapfile -t COMPREPLY < <(compgen -f -- "$cur")
                return 0
                ;;
        esac
    fi

    while IFS= read -r candidate; do
        if [[ -n $candidate && $candidate == "$cur"* && -z ${seen[$candidate]+present} ]]; then
            COMPREPLY+=("$candidate")
            seen[$candidate]=1
        fi
    done <<< "$candidates"
    return 0
}

complete -F _hyprctl hyprctl
