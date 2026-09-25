# Startup file of the diagnostics terminal's shell (bash --rcfile).
#
# The operator's own ~/.bashrc first, so the shell is theirs, then the mission's
# environment sourced inside this interactive shell: nectar-activate sources
# /opt/ros/jazzy/setup.bash, which is what registers ros2's argcomplete, so
# "ros2 to<Tab>" completes the way it does in any ROS terminal. Exported
# variables alone would give the right graph but no completion.

[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"

if [ -n "$BMG_NECTAR_ACTIVATE" ] && [ -f "$BMG_NECTAR_ACTIVATE" ]; then
    source "$BMG_NECTAR_ACTIVATE"
fi

# Kept apart from the operator's everyday history.
HISTFILE="$HOME/.bmg_terminal_history"
HISTSIZE=2000
HISTCONTROL=ignoredups

PS1='\[\e[1;32m\]\u@\h\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\$ '

# OSC 7 after every command reports the working directory to the station, so
# the title bar follows `cd` the way a desktop terminal does. The path is
# percent-encoded, as the file:// URI requires.
__bmg_report_cwd() {
    local LC_ALL=C path="$PWD" encoded="" i ch
    for (( i = 0; i < ${#path}; i++ )); do
        ch="${path:i:1}"
        case "$ch" in
            [a-zA-Z0-9/._~-]) encoded+="$ch" ;;
            *) printf -v ch '%%%02X' "'$ch"; encoded+="$ch" ;;
        esac
    done
    printf '\e]7;file://%s%s\a' "${HOSTNAME%%.*}" "$encoded"
}
PROMPT_COMMAND="__bmg_report_cwd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
