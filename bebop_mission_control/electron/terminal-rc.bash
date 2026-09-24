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

PS1='\[\e[1;32m\]operator@bmg\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\$ '
