# app/views/api/v1/profiles/show.json.jbuilder plus the Durian OS View as fields.
json.partial! 'api/v1/models/user', formats: [:json], resource: @user
json.partial! 'durian_os/view_as', formats: [:json]
