Setup
create venv with python 3.11
pip install -r requiremnts.txt


install terraform
install awscli
create aws admin user (or least priveldge with necessary rights would be: ...)
configure aws cli (username password etc)


env only stores appconfig awssecret path and aws region
all other env vars are stored as secrets

cd terraform 
terraform init
terraform plan
terrafrom apply
