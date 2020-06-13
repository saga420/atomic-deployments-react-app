# atomic-deployments-react-app

Deploy React App to AWS S3 With Cloudfront

## steps

create virtualenv

```bash
python3 -m pip install --user virtualenv
```
start virtualenv

```bash
python3 -m venv env && source env/bin/activate
```

install python requirements

```bash
pip3 install -r requirements.txt
```

run (for details see github_action/example.yml)

```bash
python3 atomic-deployments/run.py $DEPLOY_S3_BUCKET $DEPLOY_LOG_S3_BUCKET $CLOUDFRONT_DISTRIBUTION_ID $BUILD_DIR
```
