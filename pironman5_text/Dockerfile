FROM sunfounder/aarch64-pironman5:1.2.6@sha256:f56b4661857afc90515969fd80cc6c4989aff70fb0732dde4a3b7089ee13bdca
COPY text_oled.py visuals.py api.py launch.py /opt/oled-text/
COPY run.sh /run.sh
RUN chmod 755 /run.sh && /opt/pironman5/venv/bin/python -m py_compile /opt/oled-text/*.py
CMD ["/run.sh"]
