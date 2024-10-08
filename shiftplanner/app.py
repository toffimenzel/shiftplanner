from flask import Flask, Request, render_template, request, session, jsonify, send_file, redirect, url_for
from datetime import datetime, timedelta
import pandas as pd
import os, re, uuid, json, math
import threading
from planner import generate_schedule

class CustomRequest(Request):
    max_form_parts = 4000  # Increase this number as needed

app = Flask(__name__)
app.request_class = CustomRequest
app.secret_key = 'supersecretkey'  # Necessary for session handling

# Define a folder to store session data
SESSION_DATA_FOLDER = 'session_data'
if not os.path.exists(SESSION_DATA_FOLDER):
    os.makedirs(SESSION_DATA_FOLDER)


def get_session_filename():
    session_id = session.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())  # Generate a new session ID
        session['session_id'] = session_id  # Store the session ID in the cookie
    return os.path.join(SESSION_DATA_FOLDER, f"app_data_{session_id}.json")


def load_app_data():
    filename = get_session_filename()
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return json.load(f)
    else:
        return setDefaults({})  # Return a default app_data structure if no file exists


def save_app_data(app_data):
    filename = get_session_filename()
    with open(filename, 'w') as f:
        json.dump(app_data, f, cls=CustomJSONEncoder, ensure_ascii=False, indent=4)


@app.template_filter('ceil')
def ceil_filter(value):
    return math.ceil(value)

app.jinja_env.filters['ceil'] = ceil_filter

# Global variable to hold the calculation results temporarily
calculation_results = {}

def setDefaults(app_data):
    app_data.setdefault("shift_params", {})
    app_data.setdefault("persons", [])
    app_data.setdefault("roles", [])
    app_data.setdefault("role_columns", [])
    app_data.setdefault("role_availability_string", {})
    app_data.setdefault("shift_table", [])
    app_data.setdefault("shift_names", [])
    app_data.setdefault("role_experience_required", {})
    app_data.setdefault("num_assignments_per_person", 2)
    app_data.setdefault("opt_consider_travel", True)
    app_data.setdefault("opt_balance_gender", True)
    app_data.setdefault("opt_same_time_slots", True)
    app_data.setdefault("opt_max_shift_dist", True)
    app_data.setdefault("opt_enforce_shift_dist", True)
    app_data.setdefault("min_distance_between_shifts", 2)
    app_data.setdefault("opt_match_partners", True)
    app_data.setdefault("partner_bonus", 10)
    app_data.setdefault("experience_penalty", 100)
    app_data.setdefault("penalty_outside_window", 1000)
    app_data.setdefault("gender_penalty", 10)
    app_data.setdefault("penalty_for_same_time_slot", 30)
    app_data.setdefault("shift_message", "")
    app_data.setdefault("message", "")
    app_data.setdefault("not_enough_shifts", False)
    app_data.setdefault("recommended_nums_people", 0)
    app_data.setdefault("solver_timeout_sec", 30)
    return app_data


def get_unique_filename(filename):
    unique_id = uuid.uuid4()
    return f"{unique_id}_{filename}"


def parse_csv(file_path):
    try:
        # Try reading with comma delimiter first
        df = pd.read_csv(file_path, delimiter=',')
        df = df.sort_values(by='name')
    except (KeyError, pd.errors.ParserError) as e:
        try:
            # If comma fails, try with semicolon delimiter
            df = pd.read_csv(file_path, delimiter=';')
            df = df.sort_values(by='name')
        except (KeyError, pd.errors.ParserError) as e:
            # If both attempts fail, raise a custom error
            raise e
    
    # Remove any leading/trailing whitespace from column names
    df.columns = df.columns.str.strip()
    return df


def process_roles(roles):
    role_dict = {}
    experienced_roles = []
    inexperienced_roles = []
    for role in roles.split(','):
        role = role.strip()
        if role.endswith('!'):
            experienced_roles.append(role[:-1])
        else:
            inexperienced_roles.append(role)
    return experienced_roles, inexperienced_roles


def process_time_field(time_str):
    if time_str.endswith('!'):
        return time_str[:-1], True
    else:
        return time_str, False


def generate_shifts(start_timestamp, duration_hours, num_shifts):
    # Convert the Unix timestamp to a datetime object
    start_datetime = datetime.fromtimestamp(start_timestamp)
    duration_hours = int(duration_hours)

    shift_names = []
    shift_datetimes = []

    for i in range(num_shifts):
        # Calculate the start time for each shift
        shift_start = start_datetime + timedelta(hours=i * duration_hours)
        shift_end = shift_start + timedelta(hours=duration_hours)
        
        # Format the shift name to show day and hour in the desired format
        shift_name = f"{shift_start.strftime('%a %H')}-{shift_end.strftime('%H')}"
        shift_names.append(shift_name)
        
        # Store shift start as Unix timestamp
        shift_datetimes.append(int(shift_start.timestamp()))

    return shift_names, shift_datetimes


@app.route('/')
def home():
    # Load data for session
    app_data = load_app_data()
    return render_template('index.html', app_data=app_data)


@app.route('/add_new_shift_role', methods=['POST'])
def add_new_shift_role():
    app_data = load_app_data()

    # Get the new role count from the form data
    new_role_count = int(request.form.get('new_role_count', '2'))

    # Create a new role name dynamically, e.g., "Role X"
    new_role_index = len(app_data['roles']) + 1
    new_role_name = request.form.get('new_role_name', f'Role {new_role_index}')

    # Append the new role to the roles list
    app_data['roles'].append(new_role_name)

    # Also update the role_columns for person table parsing
    app_data['role_columns'].append(new_role_name)

    # Add the new role to the shift_table with uniform initial counts for all shifts
    new_role_shifts = [new_role_count] * app_data['shift_params'].get('num_shifts', 0)
    app_data['shift_table'].append(new_role_shifts)

    # Update the role_experience_required dictionary for the new role (set to False by default)
    app_data['role_experience_required'][new_role_name] = False

    # Save updated app data
    save_app_data(app_data)

    # Return the new role and shift data as JSON for the AJAX request
    return jsonify({
        'new_role_name': new_role_name,
        'new_role_shifts': new_role_shifts,
        'role_experience_required': app_data['role_experience_required']
    }), 200


@app.route('/delete_shift_role', methods=['POST'])
def delete_shift_role():
    role_index = int(request.json.get('role_index'))

    app_data = load_app_data()

    # Ensure the role_index is within a valid range
    if 0 <= role_index < len(app_data['roles']):
        # Get the role name before removing it from the list
        role_name = app_data['roles'][role_index]
        
        # Remove the role data
        app_data['roles'].pop(role_index)
        app_data['role_columns'].pop(role_index)
        app_data['shift_table'].pop(role_index)
        
        # Delete from role_experience_required dictionary
        if role_name in app_data['role_experience_required']:
            del app_data['role_experience_required'][role_name]

        # Save updated app data
        save_app_data(app_data)

        return jsonify({'status': 'success'}), 200
    else:
        return jsonify({'status': 'error', 'message': 'Invalid role index'}), 400



@app.route('/create_shift_table', methods=['POST'])
def create_shift_table():
    shift_params = {
        'shift_duration_hours': request.form.get('shift_duration_hours', ''),
        'first_shift_start_datetime': request.form.get('first_shift_start_datetime', ''),  # Updated key to match the datetime-local input name
        'num_shifts': int(request.form.get('num_shifts', '0')),
        'roles_min': request.form.get('roles_min', '')
    }

    success = True
    message = "Shift table created!"

    # Parse the datetime input string into a datetime object
    try:
        first_shift_start_datetime = datetime.strptime(shift_params['first_shift_start_datetime'], '%Y-%m-%dT%H:%M')
        
        # Convert the datetime object to a Unix timestamp
        first_shift_start_timestamp = int(first_shift_start_datetime.timestamp())
    except ValueError:
        # Handle invalid input
        app_data = load_app_data()
        app_data.update({
            'message': "Invalid date and time format. Please select a valid date and time."
        })
        return render_template('index.html', app_data=app_data)

    # Generate the shifts using the Unix timestamp
    shift_names, shift_datetimes = generate_shifts(first_shift_start_timestamp, shift_params['shift_duration_hours'], shift_params['num_shifts'])
    
    # Updated role pattern to make the bracketed value optional
    role_pattern = r'([\w/]+)(?:\(([\d,]*)\))?(!?)(,|$)'
    roles = []
    role_counts = {}
    role_experience_required = {}
    
    for match in re.findall(role_pattern, shift_params['roles_min']):
        role = match[0]
        counts_str = match[1]
        counts = list(map(int, counts_str.split(','))) if counts_str else [1]  # Default to 1 if no counts provided
        experience_required = match[2] == '!'
        
        if len(counts) == 1:
            role_counts[role] = counts * shift_params['num_shifts']
        elif len(counts) == shift_params['num_shifts']:
            role_counts[role] = counts
        else:
            message = f"Number of people per shift provided for role '{role}' is more than one, but does not match the number of shifts!"
            success = False
        
        roles.append(role)
        role_experience_required[role] = experience_required
    
    if success:
        shift_table = [[c for c in role_counts[role]] for role in roles]
    else:
        shift_table = []

    total_assignments_needed = sum(sum(role) for role in shift_table)
    recommended_nums_people = math.ceil(total_assignments_needed/int(12/int(shift_params['shift_duration_hours'])))

    # Update the session data
    app_data = load_app_data()
    app_data.update({
        'roles': roles,
        'role_counts': role_counts,
        'role_experience_required': role_experience_required,
        'shift_params': shift_params,
        'shift_names': shift_names,
        'shift_datetimes': shift_datetimes,
        'shift_table': shift_table,
        'total_assignments_needed': total_assignments_needed,
        'recommended_nums_people': recommended_nums_people,
        'message': message
    })
    save_app_data(app_data)
    
    return render_template('index.html', app_data=app_data)


'''
@app.route('/change_shift_table', methods=['POST'])
def change_shift_table():
    app_data = load_app_data()

    # Update the shift table based on form submission
    shift_table = []
    role_experience_required = {}

    for role_index in range(len(app_data.get('roles', []))):
        # Check if experience balancing is required
        role_name = app_data['roles'][role_index]
        role_experience_required[role_name] = request.form.get(f'experience_balance_{role_index}') is not None

        # Update the shift table
        shift_table.append([
            int(request.form.get(f'shift_{role_index}_{shift_index}', 0))
            for shift_index in range(app_data.get('shift_params', {}).get('num_shifts', 0))
        ])
    
    total_assignments_needed = sum(sum(role) for role in shift_table)
    recommended_nums_people = math.ceil(total_assignments_needed/int(12/int(app_data.get('shift_params', {}).get('shift_duration_hours', 0))))

    # Update the session data
    app_data.update({
        'shift_table': shift_table,
        'total_assignments_needed': total_assignments_needed,
        'recommended_nums_people': recommended_nums_people,
        'role_experience_required': role_experience_required,
        'message': "Shift table changes saved!"
    })
    save_app_data(app_data)
    
    return render_template('index.html', app_data=app_data)
'''

@app.route('/ajax_change_shift_table', methods=['POST'])
def ajax_change_shift_table():
    app_data = load_app_data()

    # Update the shift table based on form submission
    shift_table = []
    role_experience_required = {}

    for role_index in range(len(app_data.get('roles', []))):
        # Check if experience balancing is required
        role_name = app_data['roles'][role_index]
        role_experience_required[role_name] = request.form.get(f'experience_balance_{role_index}') is not None

        # Update the shift table
        shift_table.append([
            int(request.form.get(f'shift_{role_index}_{shift_index}', 0))
            for shift_index in range(app_data.get('shift_params', {}).get('num_shifts', 0))
        ])
    
    role_total_counts = [sum(role) for role in shift_table]
    total_assignments_needed = sum(sum(role) for role in shift_table)
    recommended_nums_people = math.ceil(total_assignments_needed/int(12/int(app_data.get('shift_params', {}).get('shift_duration_hours', 0))))

    # Update the session data
    app_data.update({
        'shift_table': shift_table,
        'role_total_counts': role_total_counts,
        'total_assignments_needed': total_assignments_needed,
        'recommended_nums_people': recommended_nums_people,
        'role_experience_required': role_experience_required,
        'message': "Shift table changes saved!"
    })
    save_app_data(app_data)

    # Return a simple response to acknowledge the AJAX request
    return jsonify({
        'total_assignments_needed': total_assignments_needed,
        'recommended_nums_people': recommended_nums_people
    }), 200


def compute_role_availability(app_data):
    # Initialize a dictionary to keep track of available people for each role
    role_availability = {role: 0 for role in app_data.get('roles', [])}

    # Iterate over all persons in the app_data
    for person in app_data.get('persons', []):
        # Count each role in experienced and inexperienced roles
        for role in person.get('experienced_roles', []):
            if role in role_availability:
                role_availability[role] += 1

        for role in person.get('inexperienced_roles', []):
            if role in role_availability:
                role_availability[role] += 1

    return str(role_availability)



@app.route('/create_person_table', methods=['POST'])
def create_person_table():
    app_data = load_app_data()
    roles = app_data.get('roles', [])
    persons = []
    genders = ['d', 'w', 'm']
    recommended_nums_people = app_data.get('recommended_nums_people', 3)
    print(recommended_nums_people)

    for i in range(recommended_nums_people):
        persons.append({
                'name': "Person"+str(i+1),
                'gender': genders[i%3],
                'experienced_roles': [],
                'inexperienced_roles': roles,
                'arrival_time': "",
                'arrival_hard': False,
                'departure_time': "",
                'departure_hard': False,
                'departure_time': "2",
                'preferred_partners': "",
                'options': "",
                'marker_a': False,
                'marker_b': False,
                'comment': ""
            })
        
    #print(persons)

    # Update the app data
    app_data.update({
        'message': "Empty participants table created!",
        'persons': persons,
        'role_columns': roles,  # Use roles from the shift definition
        'role_availability_string': compute_role_availability(app_data)
    })
    save_app_data(app_data)

    return render_template('index.html', app_data=app_data, scroll_to='participants')


@app.route('/load_person_table', methods=['POST'])
def load_person_table():
    csv_file = request.files.get('csv_file')
    persons = []

    app_data = load_app_data()
    roles = app_data.get('roles', [])
    role_experience_required = app_data.get('role_experience_required', {})

    if csv_file and csv_file.filename.endswith('.csv'):
        unique_filename = get_unique_filename(csv_file.filename)
        file_path = os.path.join('temp', unique_filename)
        if not os.path.exists('temp'):
            os.makedirs('temp')
        csv_file.save(file_path)
        session['uploaded_file'] = file_path
        
        try:
            # Parse the CSV file and update the data
            df = parse_csv(file_path)
            df = df.fillna('')
            df = df.sort_values(by='name')
            
            for _, row in df.iterrows():
                experienced_roles, inexperienced_roles = process_roles(row['roles'])
                arrival_time, arrival_hard = process_time_field(row['arrival_time'])
                departure_time, departure_hard = process_time_field(row['departure_time'])

                # Add gender field, defaulting to 'd' if not provided
                gender = row['gender'].strip().lower() if row['gender'].strip().lower() in ['m', 'w', 'd'] else 'd'
                
                persons.append({
                    'name': row['name'],
                    'gender': gender,
                    'experienced_roles': experienced_roles,
                    'inexperienced_roles': inexperienced_roles,
                    'arrival_time': arrival_time,
                    'arrival_hard': arrival_hard,
                    'departure_time': departure_time,
                    'departure_hard': departure_hard,
                    'num_p_shifts': row['num_p_shifts'],
                    'assign_shifts': row['assign_shifts'],
                    'veto_shifts': row['veto_shifts'],
                    'preferred_partners': row['preferred_partners'],
                    'options': row['options'],
                    'marker_a': row['marker_a'],
                    'marker_b': row['marker_b'],
                    'comment': row['comment']
                })
            
            # Delete temporary file
            os.remove(file_path)

            # Update the app data
            app_data.update({
                'message': "Participants table created from file!",
                'persons': persons,
                'role_columns': roles,  # Use roles from the shift definition
                'role_availability_string': compute_role_availability(app_data)
            })
            save_app_data(app_data)

        except (KeyError, ValueError, pd.errors.EmptyDataError) as e:
            if isinstance(e, KeyError):
                missing_column = str(e).strip("'")  # Extract the missing column name from the error message
                error_message = f"Invalid CSV file: Missing required column '{missing_column}'."
            else:
                error_message = f"Invalid CSV file: {str(e)}"
            
            # If there's an error parsing the CSV, set an error message with details
            app_data.update({
                'message': error_message,
                'persons': None,
                'role_columns': roles  # Reset roles if CSV loading fails
            })
            save_app_data(app_data)

        return render_template('index.html', app_data=app_data, scroll_to='participants')

    else:
        file_path = session.get('uploaded_file')
        file_message = "Using previously uploaded file."
        if not file_path or not os.path.exists(file_path):
            app_data.update({
                'message': "Please upload a valid CSV file.",
                'file_message': "No file uploaded or file not found",
                'persons': None,
                'role_columns': roles  # Use roles from the shift definition
            })
            save_app_data(app_data)
            return render_template('index.html', app_data=app_data)

# ajax route
@app.route('/change_person_table', methods=['POST'])
def change_person_table():
    app_data = load_app_data()

    # Retrieve current person data
    current_persons = app_data.get('persons', [])
    
    # Update the persons based on form submission
    persons = []
    for i in range(len(current_persons)):
        experienced_roles = []
        inexperienced_roles = []

        for role in app_data.get('role_columns', []):
            
            isAvailable = request.form.get(f'roles_{i}_{role}_available')
            experienced_checkbox_exists = f'roles_{i}_{role}_experienced' in request.form
            isExperienced = request.form.get(f'roles_{i}_{role}_experienced')
            knownExperienced = role in current_persons[i]['experienced_roles']

            # If the role's experienced checkbox exists and is checked
            if experienced_checkbox_exists and isExperienced:
                experienced_roles.append(role)
            # If the role's available checkbox is checked and the experienced checkbox is unchecked
            elif isAvailable and ((experienced_checkbox_exists and not isExperienced) or not experienced_checkbox_exists):
                inexperienced_roles.append(role)
            # If the role doesn't require experience balance, retain previous experienced status if it was marked as such
            elif isAvailable and not experienced_checkbox_exists and knownExperienced:
                experienced_roles.append(role)

        persons.append({
            'name': request.form.get(f'name_{i}', ''),
            'gender': request.form.get(f'gender_{i}', 'd').lower(),
            'experienced_roles': experienced_roles,
            'inexperienced_roles': inexperienced_roles,
            'arrival_time': request.form.get(f'arrival_time_{i}', ''),
            'arrival_hard': request.form.get(f'arrival_hard_{i}') is not None and len(request.form.get(f'arrival_time_{i}', '')) > 0,
            'departure_time': request.form.get(f'departure_time_{i}', ''),
            'departure_hard': request.form.get(f'departure_hard_{i}') is not None and len(request.form.get(f'departure_time_{i}', '')) > 0,
            'num_p_shifts': request.form.get(f'num_p_shifts_{i}', ''),
            'assign_shifts': request.form.get(f'assign_shifts_{i}', ''),
            'veto_shifts': request.form.get(f'veto_shifts_{i}', ''),
            'preferred_partners': request.form.get(f'preferred_partners_{i}', ''),
            'options': request.form.get(f'options_{i}', ''),
            'marker_a': request.form.get(f'marker_a_{i}', ''),
            'marker_b': request.form.get(f'marker_b_{i}', ''),
            'comment': request.form.get(f'comment_{i}', '')
        })

    num_avail_shifts = sum(int(person['num_p_shifts']) for person in persons)
    num_m = sum(person['gender'] == 'm' for person in persons)
    num_w = sum(person['gender'] == 'w' for person in persons)
    num_d = sum(person['gender'] == 'd' for person in persons)

    # Update the session data
    app_data.update({
        'persons': persons,
        'role_availability_string': compute_role_availability(app_data),
        'message': "Participant data saved successfully!"
    })
    save_app_data(app_data)
    
    #return render_template('index.html', app_data=app_data, scroll_to='participants')
    return jsonify({
            'num_persons': len(persons),
            'num_avail_shifts': num_avail_shifts,
            'num_m': num_m,
            'num_w': num_w,
            'num_d': num_d,
        }), 200


@app.route('/delete_person', methods=['POST'])
def delete_person():
    person_index = request.json.get('index')
    app_data = load_app_data()

    if person_index is not None and 0 <= person_index < len(app_data.get('persons', [])):
        del app_data['persons'][person_index]
        save_app_data(app_data)
        return jsonify({'status': 'success'}), 200

    return jsonify({'status': 'error'}), 400


@app.route('/add_person', methods=['POST'])
def add_person():
    app_data = load_app_data()
    persons = app_data.get('persons', [])

    # Clone the last person but with the name "Unknown"
    last_person = persons[-1] if persons else {
        'name': 'Unknown',
        'gender': 'd',
        'experienced_roles': [],
        'inexperienced_roles': [],
        'arrival_time': '',
        'arrival_hard': False,
        'departure_time': '',
        'departure_hard': False,
        'assign_shifts': '',
        'veto_shifts': '',
        'preferred_partners': '',
        'options': '',
        'marker_a': False,
        'marker_b': False,
        'comment': ''
    }
    new_person = last_person.copy()
    new_person['name'] = 'Unknown'
    persons.append(new_person)

    # Update the session data
    app_data['persons'] = persons
    save_app_data(app_data)

    return jsonify({
        'status': 'success',
        'person': new_person,
        'role_experience_required': app_data.get('role_experience_required', {})
    }), 200


@app.route('/generate_plan', methods=['POST'])
def generate_plan():
    app_data = load_app_data()

    # Generate a unique key for the current calculation
    calculation_key = str(uuid.uuid4())
    app_data['calculation_key'] = calculation_key
    app_data['calculation_start_time'] = datetime.utcnow().timestamp()

    app_data.update({
        'opt_consider_travel': request.form.get('opt_consider_travel', False),
        'opt_balance_gender': request.form.get('opt_balance_gender', False),
        'opt_max_shift_dist': request.form.get('opt_max_shift_dist', False),
        'opt_enforce_shift_dist': request.form.get('opt_enforce_shift_dist', False),
        'min_distance_between_shifts': request.form.get('min_distance_between_shifts', ''),
        'opt_match_partners': request.form.get('opt_match_partners', False),
        'opt_same_time_slots': request.form.get('opt_same_time_slots', False),
        'partner_bonus': request.form.get('partner_bonus', ''),
        'experience_penalty': request.form.get('experience_penalty', ''),
        'penalty_outside_window': request.form.get('penalty_outside_window', ''),
        'gender_penalty': request.form.get('gender_penalty', ''),
        'penalty_for_same_time_slot': request.form.get('penalty_for_same_time_slot', ''),
        'solver_timeout_sec': request.form.get('solver_timeout_sec', ''),
        'not_enough_shifts': False
    })
    save_app_data(app_data)

    total_available_shifts = sum(int(person['num_p_shifts']) for person in app_data.get('persons',{}))
    total_assignments_needed = app_data.get('total_assignments_needed', 0)
    plus_shifts = total_available_shifts - total_assignments_needed

    if plus_shifts < 0:
        app_data.update({
            'not_enough_shifts': True
        })
        save_app_data(app_data)
        return render_template('index.html', app_data=app_data, scroll_to='planner')
    
    else:
        # Start the calculation in a separate thread
        thread = threading.Thread(target=run_calculation, args=(calculation_key, app_data))
        thread.start()

        # Display a page to indicate that the calculation has started
        return render_template('calculation_started.html')


def run_calculation(calculation_key, app_data):
    results = generate_schedule(app_data)
    app_data.update({
        'results': results
    })
    calculation_results[calculation_key] = app_data

@app.route('/check_calculation')
def check_calculation():
    app_data = load_app_data()
    calculation_key = load_app_data().get('calculation_key')
    if calculation_key and calculation_key in calculation_results:
        app_data = calculation_results.pop(calculation_key)
        save_app_data(app_data)
        return redirect(url_for('display_results'))
    else:
        return render_template('calculation_started.html')


@app.route('/display_results')
def display_results():
    app_data = load_app_data()
    target = 'planner' if app_data.get('results', {}).get('status', "NOT FEASIBLE") == "NOT FEASIBLE" else 'results'
    return render_template('index.html', app_data=app_data, scroll_to=target)


@app.route('/download_sample_file', methods=['POST'])
def download_sample_file():
    final_file_path = os.path.join('', 'persons.csv')
    return send_file(final_file_path, as_attachment=True, download_name='sample.csv')


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


@app.route('/save_state', methods=['POST'])
def save_state():
    app_data = load_app_data()
    
    # Use a temporary file path to ensure data is written fully
    temp_file_path = os.path.join('temp', 'saved_state_temp.json')
    final_file_path = os.path.join('temp', 'saved_state.json')

    try:
        # Remove the temporary file if it already exists
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        
        # Write data to the temporary file with explicit encoding
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            json.dump(app_data, f, cls=CustomJSONEncoder, ensure_ascii=False, indent=4)
        
        # Replace the final file with the temporary file
        if os.path.exists(final_file_path):
            os.remove(final_file_path)
        os.rename(temp_file_path, final_file_path)
        
    except Exception as e:
        # Handle any errors during the save process
        print(f"Error saving state: {e}")
        return jsonify({"error": "Failed to save state"}), 500

    return send_file(final_file_path, as_attachment=True, download_name='planner.json')


import io

@app.route('/restore_state', methods=['POST'])
def restore_state():
    state_file = request.files.get('state_file')

    if state_file and state_file.filename.endswith('.json'):
        try:
            # Read file content and decode it using utf-8 first
            file_content = state_file.read().decode('utf-8')
            app_data = json.loads(file_content)
        except UnicodeDecodeError:
            # If there's a decoding error, try another encoding
            state_file.seek(0)  # Reset file pointer to the beginning
            try:
                file_content = state_file.read().decode('latin1')
                app_data = json.loads(file_content)
            except Exception as e:
                # Handle other potential errors and return an error message
                app_data = session.get('app_data', {})
                app_data.update({
                    'message': f"Failed to load state file: {str(e)}"
                })
                return render_template('index.html', app_data=app_data)

        app_data.update({
            'message': "State restored!"
        })
        save_app_data(app_data)
    else:
        app_data = session.get('app_data', {})
        app_data.update({
            'message': "Invalid state file."
        })

    return render_template('index.html', app_data=app_data)


@app.route('/load_example', methods=['POST'])
def load_example():
    try:
        # Load the example data file
        with open('example_state.json', 'r') as example_file:
            app_data = json.load(example_file)
        
        app_data.update({
            'message': "Example loaded!"
        })
        save_app_data(app_data)

    except FileNotFoundError:
        app_data = load_app_data()
        app_data.update({
            'message': "Example file not found."
        })
        save_app_data(app_data)

    return render_template('index.html', app_data=app_data)


@app.route('/reset_state', methods=['POST'])
def reset_state():
    app_data = {}
    app_data = setDefaults(app_data)
    save_app_data(app_data)
    return render_template('index.html', app_data=app_data)


@app.route('/impressum')
def impressum():
    return render_template('impressum.html')


if __name__ == '__main__':
    if not os.path.exists('temp'):
        os.makedirs('temp')
    app.run(debug=True)
