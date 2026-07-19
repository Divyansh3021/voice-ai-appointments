#!/usr/bin/env bash
# One-time LiveKit Cloud SIP setup for the clinic's single phone number.
# Requires the `lk` CLI (https://github.com/livekit/livekit-cli), authenticated
# against your LiveKit Cloud project (`lk project add` / LIVEKIT_URL+keys in env).
#
# Usage: ./scripts/setup_livekit_sip.sh +1XXXXXXXXXX
set -euo pipefail

PHONE_NUMBER="${1:?Usage: $0 <phone-number-in-e164>}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

cat > "$WORKDIR/inbound-trunk.json" <<EOF
{
  "trunk": {
    "name": "clinic-inbound",
    "numbers": ["${PHONE_NUMBER}"],
    "krisp_enabled": true
  }
}
EOF

echo "Creating inbound SIP trunk for ${PHONE_NUMBER}..."
TRUNK_ID=$(lk sip inbound create "$WORKDIR/inbound-trunk.json" | tee /dev/stderr | grep -oE 'ST_[A-Za-z0-9]+' | head -1)

if [ -z "$TRUNK_ID" ]; then
  echo "Could not parse trunk id from lk output - check it manually and update dispatch-rule.json yourself." >&2
  exit 1
fi

cat > "$WORKDIR/dispatch-rule.json" <<EOF
{
  "dispatch_rule": {
    "rule": { "dispatchRuleIndividual": { "roomPrefix": "call-" } },
    "trunk_ids": ["${TRUNK_ID}"]
  }
}
EOF

echo "Creating dispatch rule bound to trunk ${TRUNK_ID}..."
lk sip dispatch create "$WORKDIR/dispatch-rule.json"

echo "Done. Now point your Twilio Elastic SIP Trunk's Origination URL at your"
echo "LiveKit Cloud SIP URI and attach ${PHONE_NUMBER} to it."
echo ""
echo "IMPORTANT: the SIP URI is NOT derived from your LIVEKIT_URL/project"
echo "subdomain - it's a separate, independently-assigned hostname. Get the"
echo "real one from your LiveKit Cloud dashboard: Telephony page -> 'SIP URI'"
echo "field near the top (looks like sip:<random>.sip.livekit.cloud)."
