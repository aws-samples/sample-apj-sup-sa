from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import create_destination_client
from region_utils import reraise_if_fatal, error_code_of, EXISTS_ERROR_CODES
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import feature_skipped_result, is_feature_unavailable
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Create evaluation forms in destination Connect instance
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    evaluation_forms = event['evaluationForms']
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    try:
        print(f"Creating evaluation forms in instance: {destination_instance_id} in region: {destination_region}")

        created_forms = []
        skipped_forms = []
        form_mapping = {}
        # Forms already in the target (by Title), listed once before any create
        existing = ExistingIndex(connect_client, destination_instance_id, 'evaluationForms')
        if existing.error is not None and is_feature_unavailable(existing.error):
            return feature_skipped_result('evaluationForms', destination_region, 'target',
                                          error_code_of(existing.error), count=len(evaluation_forms),
                                          evaluationFormMapping={})

        for form in evaluation_forms:
            try:
                original_id = form['EvaluationFormId']
                form_title = form['Title']

                print(f"Processing evaluation form: {redact(form_title)}")

                # Validate form structure
                if not form.get('Items') or not isinstance(form['Items'], list):
                    print(f"Skipping form '{redact(form_title)}': Invalid or missing items structure")
                    skipped_forms.append({
                        'originalId': original_id,
                        'title': form_title,
                        'status': 'skipped',
                        'error': 'Invalid or missing items structure'
                    })
                    continue

                # Check if evaluation form already exists
                if existing.error is not None:
                    skipped_forms.append(existing.lookup_failure(original_id))
                    continue
                existing_form_id = existing.get(form_title)

                if existing_form_id:
                    print(f"Evaluation form '{redact(form_title)}' already exists: {existing_form_id}")
                    form_mapping[original_id] = existing_form_id
                    skipped_forms.append({
                        'originalId': original_id,
                        'newId': existing_form_id,
                        'title': form_title,
                        'status': 'existing'
                    })
                    continue

                # Clean up form items to remove read-only fields and fix automation structure
                cleaned_items = clean_form_items(form['Items'])

                # Prepare create parameters
                create_params = {
                    'InstanceId': destination_instance_id,
                    'Title': form_title,
                    'Items': cleaned_items
                }

                # Add optional parameters if they exist
                if form.get('Description'):
                    create_params['Description'] = form['Description']

                if form.get('ScoringStrategy'):
                    create_params['ScoringStrategy'] = form['ScoringStrategy']

                # Create the evaluation form
                response = connect_client.create_evaluation_form(**create_params)
                new_form_id = response['EvaluationFormId']

                # Activate the evaluation form (required for it to be usable)
                try:
                    connect_client.activate_evaluation_form(
                        InstanceId=destination_instance_id,
                        EvaluationFormId=new_form_id,
                        EvaluationFormVersion=1  # New forms start at version 1
                    )
                    print(f"Activated evaluation form: {redact(form_title)}")
                except Exception as activate_error:
                    reraise_if_fatal(activate_error)
                    print(f"Warning: Could not activate form '{redact(form_title)}': {describe_error(activate_error)}")
                    # The form exists but is not usable until activated: report it.
                    skipped_forms.append(failure_entry(original_id, activate_error, 'form created, not activated'))

                form_mapping[original_id] = new_form_id
                created_forms.append({
                    'originalId': original_id,
                    'newId': new_form_id,
                    'title': form_title,
                    'status': 'created'
                })

                print(f"Created evaluation form: {redact(form_title)} ({original_id} -> {new_form_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code_of(e) in EXISTS_ERROR_CODES:
                    print(f"Evaluation form {original_id} create failed ({error_code_of(e)}), looking the title up...")
                    existing_form_id, failure = existing.recheck(form_title, e, original_id)
                    if existing_form_id:
                        form_mapping[original_id] = existing_form_id
                        skipped_forms.append({
                            'originalId': original_id,
                            'newId': existing_form_id,
                            'title': form_title,
                            'status': 'existing'
                        })
                    else:
                        skipped_forms.append(failure)
                else:
                    print(f"Error creating evaluation form '{redact(form_title)}': {describe_error(e)}")
                    skipped_forms.append({
                        'originalId': original_id,
                        'title': form_title,
                        'status': 'failed',
                        'error': describe_error(e), 'errorCode': error_code_of(e)
                    })

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error processing evaluation form '{redact(form_title)}': {describe_error(e)}")
                skipped_forms.append({
                    'originalId': original_id,
                    'title': form_title,
                    'status': 'failed',
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })

        print(f"Successfully processed {len(created_forms)} evaluation forms")

        return {
            'statusCode': 200,
            'evaluationFormMapping': form_mapping,
            'createdForms': created_forms,
            'skippedForms': skipped_forms
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating evaluation forms: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'evaluationFormMapping': {}
        }

def clean_form_items(items):
    """
    Clean form items to remove read-only fields and fix automation structure.
    The DescribeEvaluationForm API returns some fields that are not accepted by CreateEvaluationForm.
    """
    cleaned_items = []

    for item in items:
        cleaned_item = clean_item(item)
        if cleaned_item:
            cleaned_items.append(cleaned_item)

    return cleaned_items


def clean_item(item):
    """Clean a single form item (section or question)"""
    cleaned = {}

    if 'Section' in item:
        cleaned['Section'] = clean_section(item['Section'])
    elif 'Question' in item:
        cleaned['Question'] = clean_question(item['Question'])

    return cleaned


def clean_section(section):
    """Clean a section item"""
    cleaned = {
        'Title': section['Title'],
        'RefId': section['RefId']
    }

    # Add optional fields
    if section.get('Instructions'):
        cleaned['Instructions'] = section['Instructions']

    if section.get('Weight'):
        cleaned['Weight'] = section['Weight']

    # Recursively clean nested items
    if section.get('Items'):
        cleaned['Items'] = clean_form_items(section['Items'])

    return cleaned


def clean_question(question):
    """Clean a question item"""
    cleaned = {
        'Title': question['Title'],
        'RefId': question['RefId'],
        'QuestionType': question['QuestionType']
    }

    # Add optional fields
    if question.get('Instructions'):
        cleaned['Instructions'] = question['Instructions']

    if question.get('NotApplicableEnabled') is not None:
        cleaned['NotApplicableEnabled'] = question['NotApplicableEnabled']

    if question.get('Weight'):
        cleaned['Weight'] = question['Weight']

    # Clean QuestionTypeProperties based on question type
    if question.get('QuestionTypeProperties'):
        cleaned['QuestionTypeProperties'] = clean_question_type_properties(
            question['QuestionTypeProperties'],
            question['QuestionType']
        )

    return cleaned


def clean_question_type_properties(props, question_type):
    """Clean question type properties based on question type"""
    cleaned = {}

    if question_type == 'SINGLESELECT' and 'SingleSelect' in props:
        cleaned['SingleSelect'] = clean_single_select(props['SingleSelect'])
    elif question_type == 'TEXT' and 'Text' in props:
        cleaned['Text'] = props['Text']
    elif question_type == 'NUMERIC' and 'Numeric' in props:
        cleaned['Numeric'] = clean_numeric(props['Numeric'])

    return cleaned


def clean_single_select(single_select):
    """Clean single select question properties"""
    cleaned = {}

    # Options are required
    if single_select.get('Options'):
        cleaned['Options'] = []
        for option in single_select['Options']:
            cleaned_option = {
                'RefId': option['RefId'],
                'Text': option['Text']
            }
            if option.get('Score') is not None:
                cleaned_option['Score'] = option['Score']
            if option.get('AutomaticFail') is not None:
                cleaned_option['AutomaticFail'] = option['AutomaticFail']
            cleaned['Options'].append(cleaned_option)

    # DisplayAs is optional
    if single_select.get('DisplayAs'):
        cleaned['DisplayAs'] = single_select['DisplayAs']

    # Automation is optional but requires Options if present
    if single_select.get('Automation'):
        automation = single_select['Automation']
        # Only include Automation if it has the required Options field
        if automation.get('Options') and len(automation['Options']) > 0:
            cleaned_automation = {'Options': []}
            for auto_option in automation['Options']:
                if 'RuleCategory' in auto_option:
                    cleaned_auto_option = {
                        'RuleCategory': {
                            'Category': auto_option['RuleCategory']['Category'],
                            'Condition': auto_option['RuleCategory']['Condition'],
                            'OptionRefId': auto_option['RuleCategory']['OptionRefId']
                        }
                    }
                    cleaned_automation['Options'].append(cleaned_auto_option)

            # Only add Automation if we have valid options
            if cleaned_automation['Options']:
                cleaned['Automation'] = cleaned_automation
        else:
            # Skip Automation if Options is missing or empty
            print("  Skipping Automation for SingleSelect question (missing Options)")

    return cleaned


def clean_numeric(numeric):
    """Clean numeric question properties"""
    cleaned = {}

    if numeric.get('MinValue') is not None:
        cleaned['MinValue'] = numeric['MinValue']

    if numeric.get('MaxValue') is not None:
        cleaned['MaxValue'] = numeric['MaxValue']

    if numeric.get('Options'):
        cleaned['Options'] = numeric['Options']

    if numeric.get('Automation'):
        cleaned['Automation'] = numeric['Automation']

    return cleaned
