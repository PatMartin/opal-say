#!/bin/bash

opal say --help
opal say generate "Hello, I am Opal Say" --play

# Test all voice (skip the header row)
for v in `opal say voice list | tail -n +2 | cut -f1 -d\ `
do
  echo "VOICE: $v"
  opal say generate --voice $v "hello, my name is Opal" --play
done

# Create a persona (voice must be a registered voice_id, not the model ref)
opal say persona add ryan --name "ryan" \
  --voice "en-us-ryan-high" \
  --description "A test persona"

# Managed server lifecycle (REST when reachable, local fallback when down)
opal say service log --lines 5
opal say service stop
opal say service start
opal say service log --lines 20

# Canonical config setting spellings (setting get/delete remain aliases)
opal say config setting list
opal say config setting set deployment_note "cli smoke"
opal say config setting show deployment_note
opal say config setting remove deployment_note
