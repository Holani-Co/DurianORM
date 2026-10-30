# app/views/devise/_auth.json.jbuilder plus the Durian OS View as fields.
json.data do
  json.partial! 'api/v1/models/user', formats: [:json], resource: resource
  json.partial! 'durian_os/view_as', formats: [:json]
end
