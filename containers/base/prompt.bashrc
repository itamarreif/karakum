
# karakum agent prompt: `<agent>:<path> √|?code $`. Same shape as the host
# prompt but the name is yellow (host is green) so container ≠ host at a glance.
# Appended last so it wins over the skel default. `\u` is the agent (the unix
# user is renamed to it by the image entrypoint); `\w` is the ~-relative path;
# the `$(…)` shows √ on success or ?<exit-code> on failure.
PS1='\[\e[1;33m\]\u\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\] $(c=$?; [ "$c" = 0 ] && echo "√" || echo "?$c") \$ '
