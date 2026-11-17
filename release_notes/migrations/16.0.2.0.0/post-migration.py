def migrate(cr, version):
    """Count the notes announced before the main menu announcement as seen by every user.

    Before this version a note was announced by an OdooBot chat on its first
    publish (``is_notified``); it is not announced a second time, also when it
    is unpublished now and published again later.
    """
    cr.execute(
        """
        INSERT INTO release_note_seen_user_rel (note_id, user_id)
        SELECT note.id, users.id
          FROM release_note note
          CROSS JOIN res_users users
         WHERE note.is_notified
           AND NOT users.share
        ON CONFLICT DO NOTHING
        """
    )
