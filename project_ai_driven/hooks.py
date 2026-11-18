def post_init_hook(env):
    """Give the projects and tasks of the database their keys: they were created without them."""
    env["project.project"]._set_missing_keys()
