#!/usr/bin/env bash
# Ground-station provisioning for BMG: ROS image transport plugins, GPU compute
# stacks (Intel OpenCL for OpenVINO GPU, NVIDIA 580 legacy for the MX110) and
# DDS socket buffers. Idempotent: every step checks state before acting. Never
# reboots; a kernel module change is reported and left to the operator.
#
# Usage:
#   sudo bash scripts/setup_station.sh                  # full provisioning
#   sudo bash scripts/setup_station.sh status           # report only, no changes
#   sudo bash scripts/setup_station.sh rollback-nvidia  # restore the 610 stack
#
# The MX110 (GM108, Maxwell) is only supported by the 580.xx legacy branch; the
# 610 driver from the CUDA repository ignores it. The 580 stack is installed
# headless: the Intel HD 620 keeps the display and the NVIDIA GPU is compute only.

set -euo pipefail

readonly NVIDIA_BRANCH="580"
readonly NVIDIA_VERSION="580.178.04-1ubuntu1"
readonly LEGACY_ROLLBACK_VERSION="610.57.04-1ubuntu1"
readonly PIN_FILE="/etc/apt/preferences.d/bmg-nvidia-legacy"
readonly SYSCTL_FILE="/etc/sysctl.d/60-bmg-dds.conf"
readonly NOUVEAU_FILE="/etc/modprobe.d/bmg-blacklist-nouveau.conf"
readonly APT_PACKAGES=(ros-jazzy-image-transport-plugins clinfo vainfo intel-opencl-icd)

MODE="${1:-all}"
TARGET_USER="${BMG_USER:-${SUDO_USER:-}}"

die() { echo "[SETUP] ERROR: $*" >&2; exit 1; }
log() { echo "[SETUP] $*"; }

[[ "${EUID}" -eq 0 ]] || die "run as root: sudo bash $0 ${MODE}"
[[ -n "${TARGET_USER}" && "${TARGET_USER}" != "root" ]] || die "cannot resolve the station user; set BMG_USER"
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
[[ -d "${TARGET_HOME}" ]] || die "home of ${TARGET_USER} not found"

readonly CACHE_DIR="${TARGET_HOME}/.cache/bmg"
readonly ROLLBACK_DIR="${CACHE_DIR}/rollback"
readonly LOG_FILE="${CACHE_DIR}/setup_station.log"
install -d -o "${TARGET_USER}" -g "${TARGET_USER}" "${CACHE_DIR}" "${ROLLBACK_DIR}"
touch "${LOG_FILE}"
chown "${TARGET_USER}:${TARGET_USER}" "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1
log "$(date --iso-8601=seconds) mode=${MODE} user=${TARGET_USER} kernel=$(uname -r)"

is_installed() {
    [[ "$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null)" == "ii " ]]
}

installed_version() {
    dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true
}

# Installed NVIDIA packages from a branch other than the legacy one.
foreign_nvidia_packages() {
    dpkg-query -W -f='${db:Status-Abbrev} ${Package} ${Version}\n' \
        'nvidia-*' 'libnvidia-*' 'cuda-drivers*' 'xserver-xorg-video-nvidia*' 2>/dev/null \
        | awk -v b="${NVIDIA_BRANCH}" '$1=="ii" && $3 ~ /^[0-9]+\./ && $3 !~ "^" b "\\." {print $2}' \
        | sed 's/:amd64$//' \
        | grep -Ev '^libnvidia-egl-' || true
}

snapshot() {
    local stamp
    stamp="$(date +%Y%m%dT%H%M%S)"
    dpkg -l > "${ROLLBACK_DIR}/dpkg_${stamp}.txt"
    apt-mark showhold > "${ROLLBACK_DIR}/holds_${stamp}.txt"
    dpkg -l | grep -iE 'nvidia|cuda-drivers' > "${ROLLBACK_DIR}/nvidia_${stamp}.txt" || true
    cp -a /etc/apt/preferences.d "${ROLLBACK_DIR}/preferences.d_${stamp}"
    chown -R "${TARGET_USER}:${TARGET_USER}" "${ROLLBACK_DIR}"
    log "snapshot ${ROLLBACK_DIR}/*_${stamp}"
}

step_packages() {
    local missing=()
    for pkg in "${APT_PACKAGES[@]}"; do
        is_installed "${pkg}" || missing+=("${pkg}")
    done
    if ((${#missing[@]})); then
        log "0B.1 installing: ${missing[*]}"
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
    else
        log "0B.1 packages already installed"
    fi
    [[ -x /opt/ros/jazzy/bin/fastdds ]] || die "fastdds CLI missing (ros-jazzy-fastrtps)"
    log "0B.1 fastdds: /opt/ros/jazzy/bin/fastdds"
}

step_groups() {
    local groups
    groups="$(id -nG "${TARGET_USER}")"
    if [[ " ${groups} " == *" render "* && " ${groups} " == *" video "* ]]; then
        log "0B.2 ${TARGET_USER} already in render and video"
    else
        usermod -aG render,video "${TARGET_USER}"
        log "0B.2 added ${TARGET_USER} to render,video (effective after re-login or reboot)"
    fi
}

step_sysctl() {
    # Fast DDS fragments a 1280x720 bgr8 sample (2.76 MB) into UDP datagrams;
    # the 208 KiB kernel default cannot hold one camera frame in flight.
    local desired
    desired=$'net.core.rmem_max=16777216\nnet.core.wmem_max=16777216\nnet.core.rmem_default=4194304\nnet.core.wmem_default=4194304'
    if [[ -f "${SYSCTL_FILE}" && "$(cat "${SYSCTL_FILE}")" == "${desired}" ]]; then
        log "0B.3 ${SYSCTL_FILE} already current"
    else
        printf '%s\n' "${desired}" > "${SYSCTL_FILE}"
        log "0B.3 wrote ${SYSCTL_FILE}"
    fi
    sysctl --system > /dev/null
    log "0B.3 net.core.rmem_max=$(sysctl -n net.core.rmem_max) wmem_max=$(sysctl -n net.core.wmem_max)"
}

step_dds_profile() {
    # Fast DDS profile for 2.76 MB camera samples, loaded by every process the
    # nectar environment starts. Inserted before the activator's command
    # passthrough (`exec "$@"`), where an appended export would never run.
    local activator="${TARGET_HOME}/ros2_ws/bin/nectar-activate"
    local profile
    profile="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bebop_mission_control/config/fastdds_video.xml"
    local marker="# BMG: Fast DDS profile for large camera samples"
    if [[ ! -f "${activator}" ]]; then
        log "4.3 ${activator} not found; export FASTRTPS_DEFAULT_PROFILES_FILE=${profile} manually"
        return
    fi
    if grep -qF "${marker}" "${activator}"; then
        log "4.3 ${activator} already exports the Fast DDS profile"
        return
    fi
    local block="${marker} (scripts/setup_station.sh)\nexport FASTRTPS_DEFAULT_PROFILES_FILE=\"${profile}\"\n"
    if grep -q '^# 5\. Command Execution Passthrough' "${activator}"; then
        sed -i "/^# 5\. Command Execution Passthrough/i ${block}" "${activator}"
    else
        printf '%b' "${block}" >> "${activator}"
    fi
    chown "${TARGET_USER}:${TARGET_USER}" "${activator}"
    log "4.3 ${activator} now exports FASTRTPS_DEFAULT_PROFILES_FILE"
}

step_openvino_telemetry() {
    # The OpenVINO runtime sends usage telemetry unless the user opted out, from
    # a forked child process; forked inside mission.py it inherited the
    # mission's signal handlers and held the exit after touchdown
    # (mvp_mission_bebop/engine/process_reaper.py). Official opt-out, per user.
    local consent="${TARGET_HOME}/intel/openvino_telemetry"
    if [[ -f "${consent}" && "$(cat "${consent}")" == "0" ]]; then
        log "7.x OpenVINO telemetry already opted out"
        return
    fi
    local tool="${TARGET_HOME}/ros2_ws/.venv/bin/opt_in_out"
    if [[ ! -x "${tool}" ]]; then
        log "7.x ${tool} not found; run 'opt_in_out --opt_out' in the venv manually"
        return
    fi
    sudo -u "${TARGET_USER}" -H "${tool}" --opt_out > /dev/null
    log "7.x OpenVINO telemetry opted out for ${TARGET_USER}"
}

write_pin() {
    local desired
    desired="$(cat <<'EOF'
# BMG: the GeForce MX110 (GM108) is supported only by the 580.xx legacy
# branch. Keep the CUDA repository from pulling a newer branch back in.
Package: nvidia-driver cuda-drivers nvidia-open cuda-drivers-*
Pin: release *
Pin-Priority: -1
EOF
)"
    if [[ -f "${PIN_FILE}" && "$(cat "${PIN_FILE}")" == "${desired}" ]]; then
        log "0B.4 ${PIN_FILE} already current"
    else
        printf '%s\n' "${desired}" > "${PIN_FILE}"
        log "0B.4 wrote ${PIN_FILE}"
    fi
}

hold_legacy() {
    local held=()
    mapfile -t held < <(dpkg-query -W -f='${db:Status-Abbrev} ${Package} ${Version}\n' \
        "*-${NVIDIA_BRANCH}" nvidia-persistenced nvidia-modprobe 2>/dev/null \
        | awk -v b="${NVIDIA_BRANCH}" '$1=="ii" && $3 ~ "^" b "\\." {print $2}' | sed 's/:amd64$//')
    ((${#held[@]})) || die "no ${NVIDIA_BRANCH} packages installed to hold"
    apt-mark hold "${held[@]}" > /dev/null
    log "0B.4 held: ${held[*]}"
}

ensure_nouveau_blacklisted() {
    if grep -rqsE '^\s*blacklist\s+nouveau' /etc/modprobe.d /usr/lib/modprobe.d /lib/modprobe.d; then
        log "0B.4 nouveau already blacklisted"
        return
    fi
    printf 'blacklist nouveau\noptions nouveau modeset=0\n' > "${NOUVEAU_FILE}"
    log "0B.4 wrote ${NOUVEAU_FILE}"
}

step_nvidia() {
    local foreign=()
    mapfile -t foreign < <(foreign_nvidia_packages)
    write_pin

    if is_installed "nvidia-headless-${NVIDIA_BRANCH}" && ((${#foreign[@]} == 0)); then
        log "0B.4 nvidia-headless-${NVIDIA_BRANCH} $(installed_version "nvidia-headless-${NVIDIA_BRANCH}") already installed"
    else
        snapshot
        local request=(
            "nvidia-headless-${NVIDIA_BRANCH}=${NVIDIA_VERSION}"
            "nvidia-utils-${NVIDIA_BRANCH}=${NVIDIA_VERSION}"
            "nvidia-persistenced=${NVIDIA_VERSION}"
            "nvidia-modprobe=${NVIDIA_VERSION}"
        )
        local to_remove=()
        for pkg in "${foreign[@]}"; do
            [[ "${pkg}" == "nvidia-persistenced" || "${pkg}" == "nvidia-modprobe" ]] && continue
            request+=("${pkg}-")
            to_remove+=("${pkg}")
        done
        log "0B.4 transaction: ${request[*]}"
        apt-get update
        # One transaction: removal of the foreign branch and installation of the
        # legacy one resolve together, so a failed resolution changes nothing.
        local simulation
        simulation="$(apt-get -s install "${request[@]}" 2>&1)" || { echo "${simulation}"; die "apt cannot resolve the 580 transaction"; }
        if grep -E '^Inst ' <<< "${simulation}" | grep -Ev "\(${NVIDIA_BRANCH}\." | grep -qiE 'nvidia|cuda'; then
            echo "${simulation}" | grep -E '^Inst '
            die "transaction would install a non-${NVIDIA_BRANCH} NVIDIA package"
        fi
        # Remove foreign packages first so dpkg does not collide on shared files (e.g. libnvidia-cfg.so.1)
        if ((${#to_remove[@]})); then
            log "0B.4 removing conflicting packages before install: ${to_remove[*]}"
            DEBIAN_FRONTEND=noninteractive apt-get remove -y "${to_remove[@]}"
        fi
        # nvidia-persistenced and nvidia-modprobe step down from 610 to 580.
        DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades -o Dpkg::Options::="--force-overwrite" "${request[@]}"
        local residual=()
        if ((${#foreign[@]})); then
            mapfile -t residual < <(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' "${foreign[@]}" 2>/dev/null \
                | awk '$1=="rc" {print $2}' | sed 's/:amd64$//')
            ((${#residual[@]})) && DEBIAN_FRONTEND=noninteractive apt-get purge -y "${residual[@]}"
        fi
        DEBIAN_FRONTEND=noninteractive apt-get autoremove -y --purge
    fi

    hold_legacy
    ensure_nouveau_blacklisted

    local kernel
    kernel="$(uname -r)"
    if ! dkms status 2>/dev/null | grep -qE "^nvidia/${NVIDIA_BRANCH}\.[0-9.]+, ${kernel}, [^:]+: installed"; then
        log "0B.4 building nvidia/${NVIDIA_BRANCH} for ${kernel}"
        dkms autoinstall -k "${kernel}"
    fi
    dkms status | grep -E '^nvidia/' || true
    dkms status 2>/dev/null | grep -qE "^nvidia/${NVIDIA_BRANCH}\.[0-9.]+, ${kernel}, [^:]+: installed" \
        || die "dkms has no nvidia/${NVIDIA_BRANCH} module for ${kernel}"
    update-initramfs -u -k "${kernel}"
    log "0B.4 done. Reboot required for the ${NVIDIA_BRANCH} module to load (operator action)."
}

rollback_nvidia() {
    snapshot
    local legacy=()
    mapfile -t legacy < <(dpkg-query -W -f='${db:Status-Abbrev} ${Package} ${Version}\n' \
        'nvidia-*' 'libnvidia-*' 2>/dev/null \
        | awk -v b="${NVIDIA_BRANCH}" '$1=="ii" && $3 ~ "^" b "\\." {print $2}' | sed 's/:amd64$//')
    ((${#legacy[@]})) && apt-mark unhold "${legacy[@]}" > /dev/null
    rm -f "${PIN_FILE}"
    local request=("cuda-drivers=${LEGACY_ROLLBACK_VERSION}" "nvidia-driver=${LEGACY_ROLLBACK_VERSION}")
    local to_remove=()
    for pkg in "${legacy[@]}"; do
        [[ "${pkg}" == "nvidia-persistenced" || "${pkg}" == "nvidia-modprobe" ]] && continue
        request+=("${pkg}-")
        to_remove+=("${pkg}")
    done
    apt-get update
    if ((${#to_remove[@]})); then
        DEBIAN_FRONTEND=noninteractive apt-get remove -y "${to_remove[@]}"
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades -o Dpkg::Options::="--force-overwrite" "${request[@]}"
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y --purge
    update-initramfs -u -k "$(uname -r)"
    log "rollback to ${LEGACY_ROLLBACK_VERSION} done. Reboot required (operator action)."
}

report() {
    log "--- status"
    for pkg in "${APT_PACKAGES[@]}" "nvidia-headless-${NVIDIA_BRANCH}" "nvidia-utils-${NVIDIA_BRANCH}"; do
        log "pkg ${pkg} $(installed_version "${pkg}")"
    done
    log "foreign nvidia: $(foreign_nvidia_packages | tr '\n' ' ')"
    log "holds: $(apt-mark showhold | tr '\n' ' ')"
    log "groups ${TARGET_USER}: $(id -nG "${TARGET_USER}")"
    log "rmem_max=$(sysctl -n net.core.rmem_max) wmem_max=$(sysctl -n net.core.wmem_max)"
    dkms status 2>/dev/null | grep -E '^nvidia/' | sed 's/^/[SETUP] dkms /' || true
    [[ -f "${PIN_FILE}" ]] && log "pin ${PIN_FILE} present" || log "pin ${PIN_FILE} absent"
}

case "${MODE}" in
    all)
        step_packages
        step_groups
        step_sysctl
        step_dds_profile
        step_openvino_telemetry
        step_nvidia
        report
        ;;
    status)
        report
        ;;
    rollback-nvidia)
        rollback_nvidia
        report
        ;;
    *)
        die "unknown mode ${MODE}; expected all, status or rollback-nvidia"
        ;;
esac
