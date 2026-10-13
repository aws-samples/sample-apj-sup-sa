1. [Financial] 암호화된 컬럼명 해석 필요
Question: List out the no. of districts that have female average salary is more than 6000 but less than 10000?
Evidence: A11 refers to average salary; Female mapps to gender = 'F'


 "SQL": "SELECT COUNT(DISTINCT T2.district_id)  FROM client AS T1 INNER JOIN district AS T2 ON T1.district_id = T2.district_id WHERE T1.gender = 'F' AND T2.A11 BETWEEN 6000 AND 10000",
"result": [
      [
        69
      ]




2. [Financial] 외국어(체코어) 코드 매핑 필요
Question: How many accounts who choose issuance after transaction are staying in East Bohemia region?
Evidence: A3 contains the data of region; 'POPLATEK PO OBRATU' represents for 'issuance after transaction'.


    "SQL": "SELECT COUNT(T2.account_id) FROM district AS T1 INNER JOIN account AS T2 ON T1.district_id = T2.district_id WHERE T1.A3 = 'east Bohemia' AND T2.frequency = 'POPLATEK PO OBRATU'",
"result": [
      [
        13
      ]
    ],

3. [Thrombosis Prediction] 의학적 수치 기준 (성별에 따른 차이)
Question: What is the ratio of male to female patients among all those with abnormal uric acid counts?
Evidence: male refers to SEX = 'M'; female refers to SEX = 'F'; abnormal uric acid refers to UA < = '8.0' where SEX = 'M', UA < = '6.5' where SEX = 'F'; calculation = DIVIDE(SUM(UA <= '8.0' and SEX = 'M'), SUM(UA <= '6.5 and SEX = 'F'))
    "SQL": "SELECT CAST(SUM(CASE WHEN T2.UA <= 8.0 AND T1.SEX = 'M' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN T2.UA <= 6.5 AND T1.SEX = 'F' THEN 1 ELSE 0 END) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID",
    "result": [
      [
        0.20566810835419985
      ]
    ],


4. [Thrombosis Prediction] 기호(+, -)의 의미 해석
Question: Are there more in-patient or outpatient who were male? What is the deviation in percentage?
Evidence: male refers to SEX = 'M'; in-patient refers to Admission = '+'; outpatient refers to Admission = '-'; percentage = DIVIDE(COUNT(ID) where SEX = 'M' and Admission = '+', COUNT(ID) where SEX = 'M' and Admission = '-')
    "SQL": "SELECT CAST(SUM(CASE WHEN Admission = '+' THEN 1 ELSE 0 END) AS REAL) * 100 / SUM(CASE WHEN Admission = '-' THEN 1 ELSE 0 END) FROM Patient WHERE SEX = 'M'",
    "result": [
      [
        83.17757009345794
      ]
    ],

5. [California Schools] 행정 코드(DOC=31) 매핑
Question: Which state special schools have the highest number of enrollees from grades 1 through 12?
Evidence: State Special Schools refers to DOC = 31; Grades 1 through 12 means K-12
    "SQL": "SELECT T2.School FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.DOC = 31 ORDER BY T1.`Enrollment (K-12)` DESC LIMIT 1",
    "result": [
      [
        "California School for the Deaf-Fremont"
      ]
    ],

6. [California Schools] 특정 용어('Total enrollment')의 계산 방식
Question: Please list the codes of the schools with a total enrollment of over 500.
Evidence: Total enrollment can be represented by Enrollment (K-12) + Enrollment (Ages 5-17)
"SQL": "SELECT T2.CDSCode FROM schools AS T1 INNER JOIN frpm AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.`Enrollment (K-12)` + T2.`Enrollment (Ages 5-17)` > 500",
  "result": [
      [
        "01100170109835"
      ],
      [
        "01100170112607"
      ],
      [
        "01100170124172"
      ],
      [
        "01100170125567"
      ],
      [
        "01100176001788"
      ],
      [
        "01100176002000"
      ],
      [
        "01316170131763"
      ],
      [
        "01611190111765"
      ],
      [
        "01611190119222"
      ],
      [
        "01611190122085"
      ],
      [
        "01611190126656"
      ],
      [
        "01611190130229"
      ],
      [
        "01611190130609"
      ],
      [
        "01611190132878"
      ],
      [
        "01611196090005"
      ],
      [
        "01611196090013"
      ],
      [
        "01611196090021"
      ],
      [
        "01611196090039"
      ],
      [
        "01611196090047"
      ],
      [
        "01611196090054"
      ],
      [
        "01611196090112"
      ],
      [
        "01611196090120"
      ],
      [
        "01611196100374"
      ],
      [
        "01611196110779"
      ],
      [
        "01611270130450"
      ],
      [
        "01611276090146"
      ],
      [
        "01611276090161"
      ],
      [
        "01611276095376"
      ],
      [
        "01611276116222"
      ],
      [
        "01611430122689"
      ],
      [
        "01611430122697"
      ],
      [
        "01611430131177"
      ],
      [
        "01611436056857"
      ],
      [
        "01611436056865"
      ],
      [
        "01611436090187"
      ],
      [
        "01611436090195"
      ],
      [
        "01611436090211"
      ],
      [
        "01611436090252"
      ],
      [
        "01611436090278"
      ],
      [
        "01611436090286"
      ],
      [
        "01611436090294"
      ],
      [
        "01611436090302"
      ],
      [
        "01611436090310"
      ],
      [
        "01611436090328"
      ],
      [
        "01611436097729"
      ],
      [
        "01611436105316"
      ],
      [
        "01611500132225"
      ],
      [
        "01611506090351"
      ],
      [
        "01611506090369"
      ],
      [
        "01611506090385"
      ],
      [
        "01611506090393"
      ],
      [
        "01611506090401"
      ],
      [
        "01611506090435"
      ],
      [
        "01611506090468"
      ],
] ... 엄청 김


7. [Debit Card Specializing] 날짜 포맷(YYYYMM) 변환 정보
Question: In 2012, who had the least consumption in LAM?
Evidence: Year 2012 can be presented as Between 201201 And 201212; The first 4 strings of the Date values in the yearmonth table can represent year.
    "SQL": "SELECT T1.CustomerID FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Segment = 'LAM' AND SUBSTR(T2.Date, 1, 4) = '2012' GROUP BY T1.CustomerID ORDER BY SUM(T2.Consumption) ASC LIMIT 1",
    "result": [
      [
        47273
      ]
    ],

8. [Codebase Community] 모호한 표현('Elder')의 나이 기준
Question: Among the posts owned by an elder user, how many of them have a score of over 19?
Evidence: elder users refers to Age > 65; Score of over 19 refers to Score > = 20
    "SQL": "SELECT COUNT(T1.Id) FROM posts AS T1 INNER JOIN users AS T2 ON T1.OwnerUserId = T2.Id WHERE T1.Score >= 20 AND T2.Age > 65",
    "result": [
      [
        1
      ]
    ],

9. [Card Games] 특정 조건('Incredibly powerful')의 데이터 정의
Question: Which are the cards that have incredibly powerful foils.
Evidence: incredibly poweful foils refers to cardKingdomFoilId is not null AND cardKingdomId is not null
"SQL": "SELECT id FROM cards WHERE cardKingdomFoilId IS NOT NULL AND cardKingdomId IS NOT NULL",
  "result": [
      [
        4
      ],
      [
        5
      ],
      [
        6
      ],
      [
        9
      ],
      [
        10
      ],
      [
        13
      ],
      [
        17
      ],
      [
        18
      ],
      [
        19
      ],
      [
        21
      ],
      [
        22
      ],
      [
        23
      ],
      [
        24
      ],
      [
        26
      ],
      [
        27
      ],
      [
        29
      ],
      [
        31
      ],
      [
        34
      ],
      [
        35
      ],
      [
        36
      ],
      [
        37
      ],
      [
        40
      ],
      [
        41
      ],
      [
        43
      ],
      [
        44
      ],
      [
        45
      ],
      [
        46
      ],
      [
        47
      ],
      [
        48
      ],
      [
        49
      ],
      [
        52
      ],
      [
        53
      ],
      [
        56
      ],
      [
        57
      ],
      [
        58
      ],
      [
        63
      ],
      [
        64
      ],
      [
        70
      ],
      [
        73
      ],
      [
        80
      ],
      [
        81
      ],
      [
        82
      ],
      [
        83
      ],
      [
        85
      ],
      [
        86
      ],
      [
        87
      ],
  ] ... 엄청 김

10. [Formula 1] 도메인 룰('Eliminated in first period') 정의
Question: Please list the reference names of the drivers who are eliminated in the first period in race number 20.
Evidence: driver reference name refers to driverRef; first qualifying period refers to q1; drivers who are eliminated in the first qualifying period refers to 5 drivers with MAX(q1); race number refers to raceId;
    "SQL": "SELECT T2.driverRef FROM qualifying AS T1 INNER JOIN drivers AS T2 ON T2.driverId = T1.driverId WHERE T1.raceId = 20 ORDER BY T1.q1 DESC LIMIT 5",

 "result": [
      [
        "sato"
      ],
      [
        "davidson"
      ],
      [
        "vettel"
      ],
      [
        "sutil"
      ],
      [
        "fisichella"
      ]
    ],


