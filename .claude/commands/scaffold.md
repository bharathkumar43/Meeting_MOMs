# /scaffold — Scaffold New Feature Command

Scaffolds boilerplate for a new route, template, or DB model.

## Usage
```
/scaffold route <name>
/scaffold model <ModelName>
/scaffold template <name>
```

## Arguments
- `$ARGUMENTS` — `route <name>` | `model <ModelName>` | `template <name>`

## What it generates

### `route <name>`
Adds to `app/routes.py`:
```python
@main_bp.route("/<name>")
@login_required
def <name>():
    return render_template("<name>.html")
```

### `model <ModelName>`
Adds to `app/models.py`:
```python
class <ModelName>(db.Model):
    __tablename__ = "<model_name>s"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
```
Then reminds you to run: `flask db migrate -m "add <model_name> table" && flask db upgrade`

### `template <name>`
Creates `app/templates/<name>.html`:
```html
{% extends "base.html" %}
{% block title %}<Name>{% endblock %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-lg-10">
    <h2><Name></h2>
  </div>
</div>
{% endblock %}
```
