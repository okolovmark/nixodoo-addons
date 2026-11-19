* The Gantt chart does not reschedule tasks: a violated dependency lag is
  only drawn red.
* Dependency lags count calendar days, not working days.
* Each project keeps a single baseline; saving a new one deletes the
  previous one, so there is no baseline history.
* The Gantt attribute columns cannot be sorted.
* The module ships no translations yet: it has no ``i18n`` folder, so its
  own texts and messages are not translated into the user's language.
* The board is not a record (the Team Dashboard is a client action); it
  comes with a later release.
