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
