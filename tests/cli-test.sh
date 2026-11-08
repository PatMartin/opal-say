#!/bin/bash

opal say --help
opal say "Hello, I am Opal Say"

# Test all voices
for v in `opal say voices list | cut -f1 -d\ `
do
  echo "VOICE: $v"
  opal say generate --voice $v "hello, my name is Opal" --play
done

# Create a persona
opal say persona create --name "Test Persona" \
  --voice "en_US-ryan-high" \
  --description "A test persona"