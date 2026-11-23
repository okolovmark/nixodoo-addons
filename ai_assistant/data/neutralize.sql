-- Blank the Claude models so a neutralized copy never calls Claude, whatever key
-- the host's environment holds, and the mail recipients so it never mails the
-- real mailboxes parked in them; the keys stay for re-pointing.
UPDATE ir_config_parameter
   SET value = ''
 WHERE key IN (
       'ai_assistant.claude_models',
       'ai_assistant.digest_recipients',
       'ai_assistant.incident_emails'
 );
