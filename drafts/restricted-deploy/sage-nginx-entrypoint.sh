#!/bin/sh
# Raw Nginx diagnostics have only this private, memory-backed FIFO as a sink.
# Docker receives fixed diagnostic classes, never untrusted request context.
set -u
umask 077

directory=$(mktemp -d /tmp/sage-nginx-log.XXXXXX 2>/dev/null) || {
    printf '%s\n' '[alert] nginx log protection could not create its private channel' >&2
    exit 1
}
fifo=$directory/error
if ! mkfifo "$fifo" 2>/dev/null; then
    rmdir "$directory" 2>/dev/null
    printf '%s\n' '[alert] nginx log protection could not create its private channel' >&2
    exit 1
fi

awk '
{
    prefix = "[warn] "
    message = $0
    if (match($0, /^[0-9][0-9][0-9][0-9]\/[0-9][0-9]\/[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] \[(emerg|alert|crit|error|warn|notice|info|debug)\] [0-9]+#[0-9]+: /)) {
        message = substr($0, RLENGTH + 1)
        # Only the validated severity enum survives. Docker timestamps the
        # received record; an input path can contain a forged date/newline.
        prefix = substr($0, 21, index($0, "]") - 20) " "
        sub(/^\*[0-9]+ /, "", message)
    } else if (match($0, /^nginx: \[(emerg|alert|crit|error|warn|notice|info|debug)\] /)) {
        prefix = substr($0, 8, RLENGTH - 8) " "
        message = substr($0, RLENGTH + 1)
    }

    # This is an allowlist of constant reasons, not removal of selected query
    # keys. Paths, request lines, upstream URLs, host and Referer never pass.
    reason = "nginx diagnostic redacted: unrecognized message"
    if (message ~ /^connect\(\) failed \([0-9]+: /)
        reason = "connect() failed while connecting to upstream"
    else if (message ~ /^upstream timed out \([0-9]+: /)
        reason = "upstream timed out"
    else if (message ~ /^recv\(\) failed \([0-9]+: /)
        reason = "recv() failed while reading from upstream"
    else if (message ~ /^send\(\) failed \([0-9]+: /)
        reason = "send() failed while writing to upstream"
    else if (message ~ /^upstream prematurely closed connection/)
        reason = "upstream prematurely closed connection"
    else if (message ~ /^upstream sent (invalid|too big) header/)
        reason = "upstream sent invalid or oversized response header"
    else if (message ~ /^no live upstreams/)
        reason = "no live upstreams"
    else if (message ~ /^client intended to send too large body:/)
        reason = "client request body exceeds configured limit"
    else if (message ~ /^open\(\) /)
        reason = "open() failed"
    else if (message ~ /^bind\(\) to /)
        reason = "bind() failed"
    else if (message ~ /^cannot load certificate/)
        reason = "cannot load TLS certificate or key"
    else if (message ~ /^host not found in upstream /)
        reason = "upstream host could not be resolved"
    else if (message ~ /^unknown directive /)
        reason = "unknown Nginx configuration directive"
    else if (message ~ /^invalid number of arguments in /)
        reason = "invalid Nginx configuration argument count"
    else if (message ~ /^signal process started/)
        reason = "signal process started"
    else if (message ~ /^nginx: the configuration file .* syntax is ok$/) {
        prefix = "[notice] "
        reason = "configuration syntax is ok"
    } else if (message ~ /^nginx: configuration file .* test is successful$/) {
        prefix = "[notice] "
        reason = "configuration test is successful"
    }

    errno = ""
    if ((reason == "connect() failed while connecting to upstream" ||
         reason == "upstream timed out" || reason == "recv() failed while reading from upstream" ||
         reason == "send() failed while writing to upstream") &&
        match(message, /\([0-9]+:/) && RLENGTH <= 5) {
        number = substr(message, RSTART + 1, RLENGTH - 2) + 0
        # This pinned Linux image uses errno values 1..133, never arbitrary
        # numbers from request content (for example a numeric login code).
        if (number >= 1 && number <= 133)
            errno = " errno=" number
    }
    else if (reason == "open() failed" || reason == "bind() failed") {
        if (message ~ / failed \(2: No such file or directory\)/)
            errno = " errno=2"
        else if (message ~ / failed \(13: Permission denied\)/)
            errno = " errno=13"
        else if (message ~ / failed \(98: Address in use\)/)
            errno = " errno=98"
    }
    print prefix reason errno
    fflush()
}
' < "$fifo" >&2 2>/dev/null &
filter_pid=$!
nginx "$@" 2> "$fifo" &
nginx_pid=$!
shutdown=0
trap 'shutdown=1' TERM INT QUIT
trap 'kill -HUP "$nginx_pid" 2>/dev/null' HUP

running() {
    kill -0 "$1" 2>/dev/null || return 1
    # A stopped or zombie filter is not healthy, even if kill -0 succeeds.
    read -r process_id process_name process_state process_rest < "/proc/$1/stat" 2>/dev/null || return 1
    case "$process_state" in T|t|Z|X|x) return 1 ;; esac
}

status=0
while [ "$shutdown" -eq 0 ]; do
    if ! running "$nginx_pid"; then
        case "${process_state-}" in
            T|t) printf '%s\n' '[alert] Nginx process stopped unexpectedly' >&2; status=1 ;;
        esac
        break
    fi
    if ! running "$filter_pid"; then
        printf '%s\n' '[alert] nginx log protection unavailable; stopping Nginx' >&2
        status=1
        break
    fi
    sleep 0.1
done

# The master forwards termination to its workers. Continue stopped processes so
# they can handle TERM, and bound cleanup so Docker never waits indefinitely.
kill -CONT "$nginx_pid" 2>/dev/null
kill -TERM "$nginx_pid" 2>/dev/null
attempt=0
while running "$nginx_pid" && [ "$attempt" -lt 50 ]; do
    sleep 0.1
    attempt=$((attempt + 1))
done
kill -KILL "$nginx_pid" 2>/dev/null
wait "$nginx_pid" 2>/dev/null
nginx_status=$?
if [ "$status" -eq 0 ] && [ "$shutdown" -eq 0 ]; then
    status=$nginx_status
fi

# A normally closed writer lets the filter flush to EOF before it exits.
attempt=0
while running "$filter_pid" && [ "$attempt" -lt 50 ]; do
    sleep 0.1
    attempt=$((attempt + 1))
done
if running "$filter_pid"; then
    printf '%s\n' '[alert] nginx log protection did not finish; stopping the filter' >&2
    status=1
fi
kill -CONT "$filter_pid" 2>/dev/null
kill -TERM "$filter_pid" 2>/dev/null
kill -KILL "$filter_pid" 2>/dev/null
wait "$filter_pid" 2>/dev/null
filter_status=$?
if [ "$filter_status" -ne 0 ] && [ "$shutdown" -eq 0 ]; then
    printf '%s\n' '[alert] nginx log protection exited abnormally' >&2
    status=1
fi
rm -f "$fifo" 2>/dev/null
rmdir "$directory" 2>/dev/null
exit "$status"
