import logging
import os
from app import create_app

log_level = logging.DEBUG if os.getenv("FLASK_DEBUG", "false").lower() == "true" else logging.INFO
logging.basicConfig(level=log_level)

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5100)
